"""Agent-based model tests: identity, speech acts, environment, tick simulation."""
import unittest

from helpers import NOW, World, plan_response

from clinic_agent.abm import AgentMessage, build_society
from clinic_agent.safety import detect_emergency


class SocietyStructureTests(unittest.TestCase):
    def setUp(self):
        self.w = World()
        self.s = self.w.society

    def tearDown(self):
        self.w.close()

    def test_society_contains_every_required_role(self):
        roles = {a.role for a in self.s.agents.values()}
        self.assertEqual(
            roles,
            {"patient", "coordinator", "safety", "scheduler", "receptionist", "policy", "memory", "doctor"},
        )
        self.assertGreaterEqual(self.s.env_count("doctor"), 6)
        self.assertGreaterEqual(self.s.env_count("patient"), 3)
        snap = self.s.snapshot()
        self.assertIn("occupancy", snap["environment"])
        self.assertTrue(snap["agents"])

    def test_each_agent_has_identity_attributes_and_intentions(self):
        for agent in self.s.agents.values():
            self.assertTrue(agent.agent_id)
            self.assertTrue(agent.name)
            self.assertTrue(agent.role)
            self.assertIsInstance(agent.attributes, dict)
            self.assertIsInstance(agent.intentions, list)
            self.assertEqual(agent.state, "idle")

    def test_message_bus_delivers_to_the_recipient_mailbox(self):
        msg = AgentMessage(
            sender="coordinator", receiver="scheduler", performative="DELEGATE",
            content={"task": "find_slots"},
        )
        self.s.dispatch(msg)
        self.assertEqual(self.s.get("scheduler").inbox, [msg])
        self.assertEqual(self.s.transcript[0].sender, "coordinator")

    def test_safety_officer_refuses_emergencies_and_clears_ordinary_text(self):
        refuse = self.s.screen("I have chest pain")
        self.assertEqual(refuse.performative, "REFUSE")
        self.assertEqual(refuse.sender, "safety")
        clear = self.s.screen("I would like a dermatology slot on Wednesday")
        self.assertEqual(clear.performative, "CONFIRM")

    def test_doctor_leave_broadcasts_from_the_doctor_agent(self):
        result = self.s.apply_doctor_leave("P1001")
        self.assertIsNotNone(result)
        docs = [a for a in self.s.agents.values() if a.role == "doctor" and a.beliefs.get("on_leave")]
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0].state, "unavailable")
        self.assertTrue(any(m.performative == "INFORM" and m.content.get("event") == "doctor_leave"
                            for m in self.s.transcript))


class TickSimulationTests(unittest.TestCase):
    def setUp(self):
        self.w = World()

    def tearDown(self):
        self.w.close()

    def test_run_day_produces_bookings_and_speech_acts(self):
        before = self.w.society.env.occupancy()["open"]
        out = self.w.society.run_day(ticks=10, extra_patients=6)
        self.assertEqual(out["ticks"], 10)
        self.assertTrue(out["log"])
        snap = out["snapshot"]
        self.assertGreaterEqual(snap["environment"]["metrics"]["messages"], 10)
        self.assertGreaterEqual(snap["by_role"]["patient"], 6)
        after = snap["environment"]["occupancy"]["open"]
        self.assertLessEqual(after, before)
        performatives = {m["performative"] for m in snap["messages"]}
        self.assertTrue({"REQUEST", "PROPOSE", "CONFIRM"} & performatives)

    def test_build_society_is_deterministic_enough_for_tests(self):
        s = build_society(self.w.clinic_db, NOW, detect_emergency)
        self.assertEqual(s.get("coordinator").attributes["uses_llm"], True)
        self.assertEqual(s.get("receptionist").attributes["requires_human_confirm"], True)


class AgentLoopUsesSocietyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.w = World()

    def tearDown(self):
        self.w.close()

    async def test_live_agent_emits_agent_messages_when_it_delegates(self):
        from clinic_agent.llm import ScriptedLLM

        llm = ScriptedLLM([
            plan_response({"action": "find_slots", "department": "Dermatology"}),
        ])
        agent = await self.w.agent(llm)
        self.assertIsNotNone(agent.society)
        await agent.respond("Any dermatology slots?")
        kinds = [e["kind"] for e in agent.tracer.events]
        self.assertIn("agent_message", kinds)
        talks = [e for e in agent.tracer.events if e["kind"] == "agent_message"]
        self.assertTrue(any(t.get("from") == "coordinator" and t.get("to") == "scheduler" for t in talks))
        self.assertGreaterEqual(agent.society.get("scheduler").messages_handled, 1)
