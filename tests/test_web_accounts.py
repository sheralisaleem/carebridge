import unittest
from dataclasses import replace

from starlette.testclient import TestClient

from helpers import NOW, World
from clinic_agent.auth import Accounts, password_hash
from clinic_agent.clinic_db import connect
from clinic_agent.config import get_settings
from clinic_agent.llm import ScriptedLLM
from clinic_agent.web import create_app


class AccountWebTests(unittest.TestCase):
    def setUp(self):
        self.world = World()
        self.addCleanup(self.world.close)
        folder = self.world.clinic_db.parent
        self.outbox = []
        self.origin = "http://127.0.0.1:8000"
        self.settings = replace(
            get_settings(), clinic_db=self.world.clinic_db, memory_db=folder / "accounts-memory.db",
            chroma_dir=folder / "chroma", log_dir=folder / "logs", clinic_now=NOW,
            embedding_backend="hash", rag_min_score=0.12, site_origin=self.origin, demo_doctor_signup=True,
        )
        app = create_app(self.settings, llm=ScriptedLLM([]), mail=lambda *item: self.outbox.append(item))
        self.client = self.enterContext(TestClient(app, base_url=self.origin))
        self.accounts = Accounts(self.world.clinic_db)
        self.addCleanup(self.client.cookies.clear)

    def post(self, path, values):
        return self.client.post(path, data={"csrf": self.client.cookies["cb_csrf"], **values},
                                headers={"Origin": self.origin}, follow_redirects=False)

    def test_agent_overview_lists_workflow_agents_with_real_source(self):
        self.assertEqual(self.client.get("/api/agents/overview").status_code, 401)
        self.accounts.attach_fixture("patient", "P1001", "sara@example.test", "valid-password")
        self.client.get("/sign-in")
        self.post("/sign-in", {"email": "sara@example.test", "password": "valid-password"})
        overview = self.client.get("/api/agents/overview").json()
        self.assertCountEqual((agent["role"] for agent in overview["agents"]),
                              ["coordinator", "safety", "scheduler", "receptionist", "policy", "memory"])
        self.assertTrue(all("beliefs" not in item for item in overview["agents"]))
        for path in ("/agents", "/assistant"):
            page = self.client.get(path)
            self.assertEqual(page.status_code, 200)
            self.assertIn('id="agent-dialog"', page.text)
            self.assertIn("clinic_agent/agents/front_desk.py", page.text)
            self.assertIn("context.waiting(", page.text)
            self.assertNotIn("clinic_agent/agents/doctor.py", page.text)
            self.assertNotIn("clinic_agent/agents/patient.py", page.text)

    def test_password_signup_preserves_booking_return_and_sends_no_email(self):
        self.client.get("/sign-up")
        request = self.post("/sign-up", {"name": "Jamie Demo", "email": "jamie@example.test",
                                         "password": "valid-password", "next": "/book?doctor_id=D01"})
        self.assertEqual(request.status_code, 303)
        self.assertEqual(request.headers["location"], "/book?doctor_id=D01")
        session = self.client.cookies["carebridge"]
        self.assertEqual(self.accounts.user_for_session(session)["role"], "patient")
        self.assertEqual(self.client.get("/book?doctor_id=D01").status_code, 200)
        self.assertEqual(self.client.get("/doctor/availability", follow_redirects=False).status_code, 403)
        self.assertEqual(self.outbox, [])

    def test_password_reset_revokes_previous_session_and_is_single_use(self):
        self.accounts.attach_fixture("patient", "P1001", "sara@example.test", "valid-password")
        self.client.get("/sign-in")
        self.post("/sign-in", {"email": "sara@example.test", "password": "valid-password"})
        session = self.client.cookies["carebridge"]
        self.assertEqual(self.post("/forgot-password", {"email": "sara@example.test"}).status_code, 200)
        email, token = self.outbox[-1]
        self.assertEqual(email, "sara@example.test")
        self.assertEqual(self.post("/reset-password", {"token": token, "password": "new-valid-password"}).status_code, 200)
        self.assertIsNone(self.accounts.user_for_session(session))
        self.assertEqual(self.post("/reset-password", {"token": token, "password": "another-password"}).status_code, 400)
        self.assertEqual(self.post("/sign-in", {"email": email, "password": "new-valid-password"}).status_code, 303)

    def test_legacy_pending_account_can_sign_in_with_its_password(self):
        with connect(self.world.clinic_db) as conn:
            conn.execute(
                "INSERT INTO accounts (email, name, role, password_hash) VALUES (?, ?, ?, ?)",
                ("pending@example.test", "Pending User", "patient", password_hash("valid-password")),
            )
        self.client.get("/sign-in")
        login = self.post("/sign-in", {"email": "pending@example.test", "password": "valid-password"})
        self.assertEqual(login.headers["location"], "/dashboard")
        self.assertIsNotNone(self.accounts.user_for_session(self.client.cookies["carebridge"])["patient_id"])

    def test_doctor_role_and_schedule_own_scope(self):
        self.accounts.attach_fixture("doctor", "D01", "amina@example.test", "valid-password")
        self.client.get("/sign-in")
        login = self.post("/sign-in", {"email": "amina@example.test", "password": "valid-password"})
        self.assertEqual(login.headers["location"], "/doctor/appointments")
        agenda = self.client.get("/doctor/appointments?doctor_id=D04").text
        self.assertIn("Sara Ahmed", agenda)
        self.assertNotIn("Omar Siddiqui", agenda)
        block = self.post("/doctor/availability", {
            "operation": "block", "date": "2026-11-12", "start_time": "09:30", "doctor_id": "D04",
        })
        self.assertEqual(block.status_code, 400)
        self.assertIn("stays on your calendar", block.text)
        forbidden = self.client.post("/doctor/availability", data={"operation": "hours"})
        self.assertEqual(forbidden.status_code, 400)

    def test_doctor_signup_goes_straight_to_agenda(self):
        self.client.get("/doctor/sign-up")
        registration = self.post("/doctor/sign-up", {
            "name": "Dr. Demo", "email": "doctor@example.test", "password": "valid-password",
            "department": "General Medicine",
        })
        self.assertEqual(registration.headers["location"], "/doctor/appointments")
        self.assertEqual(self.client.get("/doctor/availability").status_code, 200)
        self.assertEqual(self.outbox, [])

    def test_deprecated_auth_pages_are_gone(self):
        self.assertNotIn("Continue with Google", self.client.get("/sign-in").text)
        for path in ("/auth/google", "/auth/google/callback", "/verify-email", "/complete-profile"):
            self.assertEqual(self.client.get(path, follow_redirects=False).status_code, 404)
