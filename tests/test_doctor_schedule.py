import concurrent.futures
import unittest

from helpers import NOW, World
from clinic_agent.auth import Accounts
from clinic_agent.doctor_schedule import DoctorSchedule, ScheduleError


class DoctorScheduleTests(unittest.TestCase):
    def setUp(self):
        self.world = World()
        self.addCleanup(self.world.close)
        self.schedule = DoctorSchedule(self.world.clinic_db, NOW)
        self.schedule.prepare()

    def test_doctor_signup_creates_empty_schedule(self):
        accounts = Accounts(self.world.clinic_db)
        accounts.prepare()
        user = accounts.register("Dr. Zara Noor", "zara@example.test", "valid-password",
                                           "doctor", "General Medicine")
        self.assertEqual(user["role"], "doctor")
        self.assertEqual(self.schedule.availability(user["doctor_id"], "2026-11-10")["hours"], [])
        self.schedule.set_slot(user["doctor_id"], "2026-11-10", "09:00", True)
        options = self.world.service.find_available_slots(doctor_id=user["doctor_id"])["slots"]
        self.assertEqual([(slot["date"], slot["start_time"]) for slot in options], [("2026-11-10", "09:00")])
        result = self.world.service.book_appointment("P1001", options[0]["slot_id"])
        self.assertTrue(result["ok"])
        self.assertEqual(self.schedule.appointments(user["doctor_id"])[0]["patient"], "Sara Ahmed")
        self.assertFalse(self.schedule.appointments("D05"))

    def test_confirmed_visit_is_kept_then_release_respects_new_hours(self):
        outcome = self.schedule.set_weekly_hours("D01", [])
        self.assertEqual(outcome["kept"], 1)
        self.assertTrue(self.schedule.appointments("D01")[0]["outside_hours"])
        self.assertFalse(self.world.service.find_available_slots(doctor_id="D01")["slots"])
        cancelled = self.world.service.cancel_appointment("P1001", "A-1001")
        self.assertTrue(cancelled["ok"])
        self.assertEqual(self.world.slot_status("D01-20261112-0930"), "blocked")

    def test_override_survives_weekly_edit_and_clinic_leave(self):
        self.schedule.set_slot("D05", "2026-11-12", "09:00", True)
        self.schedule.set_weekly_hours("D05", [])
        slots = self.world.service.find_available_slots(doctor_id="D05")["slots"]
        self.assertEqual([slot["slot_id"] for slot in slots], ["D05-20261112-0900"])
        from clinic_agent.clinic_db import apply_doctor_leave

        apply_doctor_leave(self.world.clinic_db, "D05", "2026-11-12")
        with self.assertRaises(ScheduleError):
            self.schedule.set_slot("D05", "2026-11-12", "09:00", True)
        self.assertFalse(self.world.service.find_available_slots(doctor_id="D05")["slots"])

    def test_rejects_overlapping_hours_and_out_of_grid_slot(self):
        with self.assertRaises(ScheduleError):
            self.schedule.set_weekly_hours("D05", [
                {"weekday": 0, "start_time": "09:00", "end_time": "11:00"},
                {"weekday": 0, "start_time": "10:00", "end_time": "12:00"},
            ])
        with self.assertRaises(ScheduleError):
            self.schedule.set_slot("D05", "2026-11-12", "09:15", True)

    def test_booking_and_block_serialize(self):
        selected = self.world.first_open_slot()

        def block():
            try:
                self.schedule.set_slot(selected["doctor_id"], selected["date"], selected["start_time"], False)
                return True
            except ScheduleError:
                return False

        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            block_future = pool.submit(block)
            book_future = pool.submit(self.world.service.book_appointment, "P1001", selected["slot_id"])
        blocked, booked = block_future.result(), book_future.result()["ok"]
        self.assertNotEqual(blocked, booked)
        self.assertEqual(self.world.slot_status(selected["slot_id"]), "blocked" if blocked else "booked")
