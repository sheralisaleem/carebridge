import unittest

from helpers import World, decline, plan_response
from clinic_agent.clinic_db import set_fault
from clinic_agent.llm import ScriptedLLM, text_response


class AgentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.world = World()
        self.addCleanup(self.world.close)

    async def test_booking_without_details_asks_for_doctor_and_uses_the_reply(self):
        llm = ScriptedLLM([
            plan_response({"action": "clarify"}),
            plan_response({"action": "find_slots", "department": "Dermatology"}),
        ])
        agent = await self.world.agent(llm)
        reply = await agent.respond("book an appointment for me")
        self.assertEqual(reply.text, "Which doctor or department would you like to book with?")
        self.assertFalse(self.world.mcp.calls)
        self.assertEqual(len(llm.calls), 1)
        listing = await agent.respond("Dermatology")
        self.assertTrue(listing.slots)
        self.assertTrue(all(slot["department"] == "Dermatology" for slot in listing.slots))
        self.assertIn(reply.text, [message["content"] for message in llm.calls[1]["messages"]])

    async def test_first_visit_booking_asks_about_timing(self):
        llm = ScriptedLLM([plan_response({"action": "start_booking"})])
        agent = await self.world.agent(llm, patient_id="P1003")
        reply = await agent.respond("Please book a visit for me.")
        self.assertEqual(reply.text, "Your first visit is with General Medicine. What day or time works for you?")
        self.assertIn('"new_patient": true', llm.calls[0]["messages"][-1]["content"])
        self.assertFalse(self.world.mcp.calls)

    async def test_one_model_call_and_actual_specialist_execution(self):
        llm = ScriptedLLM([plan_response({"action": "find_slots", "department": "Dermatology"})])
        agent = await self.world.agent(llm)
        reply = await agent.respond("Find a dermatologist.")
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(llm.calls[0]["tools"], ["submit_plan"])
        self.assertTrue(reply.slots)
        events = agent.tracer.events
        start = next(i for i, event in enumerate(events) if event["kind"] == "agent_message" and event["performative"] == "DELEGATE")
        tool = next(i for i, event in enumerate(events) if event["kind"] == "tool_result")
        end = next(i for i, event in enumerate(events) if event["kind"] == "agent_message" and event.get("from") == "scheduler")
        self.assertLess(start, tool)
        self.assertLess(tool, end)
        self.assertEqual(events[end]["in_reply_to"], events[start]["id"])

    async def test_slot_ordinal_and_human_approval(self):
        llm = ScriptedLLM([
            plan_response({"action": "find_slots", "department": "Dermatology"}),
            plan_response({"action": "book", "option": 2}),
        ])
        agent = await self.world.agent(llm)
        listing = await agent.respond("Find dermatology visits.")
        chosen = listing.slots[1]["slot_id"]
        booked = await agent.respond("Book the second one.")
        self.assertIn("Appointment confirmed", booked.text)
        self.assertEqual(self.world.slot_status(chosen), "booked")
        self.assertEqual(len(llm.calls), 2)
        self.assertIn(chosen, llm.calls[1]["messages"][-1]["content"])
        self.assertEqual(self.world.mcp.calls[-1][1]["patient_id"], "P1001")

    async def test_compound_preference_search_and_override(self):
        llm = ScriptedLLM([
            plan_response({"action": "remember", "category": "scheduling_preference", "key": "part_of_day", "value": "morning"},
                          {"action": "find_slots", "department": "Dermatology"}),
            plan_response({"action": "find_slots", "department": "Dermatology", "part_of_day": "afternoon"}),
        ])
        first = await self.world.agent(llm)
        response = await first.respond("Remember I prefer mornings and look for dermatology.")
        self.assertTrue(response.slots)
        self.assertEqual(self.world.mcp.calls[-1][1]["part_of_day"], "morning")
        first.end_session()
        new = await self.world.agent(llm)
        response = await new.respond("This time try afternoon.")
        self.assertTrue(response.slots)
        self.assertEqual(self.world.mcp.calls[-1][1]["part_of_day"], "afternoon")
        self.assertIn("morning", llm.calls[-1]["messages"][-1]["content"])

    async def test_decline_and_missing_callback_do_not_mutate(self):
        slot = self.world.first_open_slot()
        for callback in (decline, None):
            agent = await self.world.agent(ScriptedLLM([]), confirm=callback)
            reply = await agent.respond_action({"action": "book", "slot_id": slot["slot_id"]})
            self.assertIn("declined", reply.text)
            self.assertEqual(self.world.slot_status(slot["slot_id"]), "open")
        self.assertFalse(any(name == "book_appointment" for name, _ in self.world.mcp.calls))
        self.assertFalse(self.world.ltm.audit_rows(patient_id="P1001")[-1]["human_confirmed"])

    async def test_other_patient_cannot_change_appointment(self):
        agent = await self.world.agent(ScriptedLLM([]))
        reply = await agent.respond_action({"action": "cancel", "appointment_id": "A-1002"})
        self.assertIn("Select the appointment", reply.text)
        self.assertFalse(any(name == "cancel_appointment" for name, _ in self.world.mcp.calls))

    async def test_patient_id_and_invalid_later_task_are_rejected_before_dispatch(self):
        llm = ScriptedLLM([plan_response(
            {"action": "find_slots", "department": "Dermatology"},
            {"action": "list_appointments", "patient_id": "P1002"},
        )])
        agent = await self.world.agent(llm)
        result = await agent.respond("Show another patient's data.")
        self.assertEqual(result.guardrail, "invalid_plan")
        self.assertFalse(self.world.mcp.calls)

    async def test_model_cannot_invent_slot_reference(self):
        slot = self.world.first_open_slot()
        llm = ScriptedLLM([plan_response({"action": "book", "slot_id": slot["slot_id"]})])
        agent = await self.world.agent(llm)
        reply = await agent.respond("What times are free?")
        self.assertIn("Choose a time", reply.text)
        self.assertFalse(self.world.mcp.calls)

    async def test_race_replans_without_another_model_call(self):
        llm = ScriptedLLM([plan_response({"action": "find_slots", "department": "Dermatology"})])
        agent = await self.world.agent(llm)
        listing = await agent.respond("Find dermatology visits.")
        set_fault(self.world.clinic_db, "race:P1001")
        reply = await agent.respond_action({"action": "book", "slot_id": listing.slots[0]["slot_id"]})
        self.assertTrue(reply.slots)
        self.assertEqual(agent.replans, 1)
        self.assertEqual(len(llm.calls), 1)
        self.assertNotEqual(reply.slots[0]["slot_id"], listing.slots[0]["slot_id"])
        self.assertNotIn("Appointment confirmed", reply.text)

    async def test_doctor_leave_offers_replacement(self):
        self.world.society.apply_doctor_leave("P1001")
        agent = await self.world.agent(ScriptedLLM([]))
        reply = await agent.respond_action({"action": "list_appointments"})
        self.assertEqual(agent.replans, 1)
        self.assertTrue(reply.slots)
        self.assertEqual(reply.reschedule_id, "A-1001")

    async def test_reschedule_and_cancel_require_separate_approvals(self):
        asked = []

        async def confirm(summary):
            asked.append(summary)
            return len(asked) == 1

        agent = await self.world.agent(ScriptedLLM([]), confirm=confirm)
        choices = await agent.respond_action({"action": "reschedule", "appointment_id": "A-1001"})
        self.assertEqual(choices.reschedule_id, "A-1001")
        updated = await agent.respond_action({"action": "reschedule", "appointment_id": "A-1001",
                                              "slot_id": choices.slots[0]["slot_id"]})
        self.assertIn("Appointment rescheduled", updated.text)
        diary = self.world.service.list_patient_appointments("P1001")["appointments"]
        new = next(visit for visit in diary if visit["status"] == "confirmed")
        result = await agent.respond_action({"action": "cancel", "appointment_id": new["appointment_id"]})
        self.assertIn("declined", result.text)
        self.assertEqual(len(asked), 2)
        self.assertIn("RESCHEDULE", asked[0])
        self.assertIn("CANCEL", asked[1])

    async def test_policy_evidence_and_insufficient_evidence(self):
        llm = ScriptedLLM([
            plan_response({"action": "policy", "query": "late cancellation fee"}),
            plan_response({"action": "policy", "query": "qzxqzxqzx"}),
        ])
        agent = await self.world.agent(llm)
        answer = await agent.respond("What is the late cancellation fee?")
        self.assertIn("Source:", answer.text)
        answer = await agent.respond("qzxqzxqzx")
        self.assertIn("couldn't find", answer.text)

    async def test_emergency_bypasses_model_and_tools(self):
        provider = ScriptedLLM([])
        agent = await self.world.agent(provider)
        result = await agent.respond("I have chest pain.")
        self.assertEqual(result.guardrail, "emergency")
        self.assertFalse(provider.calls or self.world.mcp.calls)

    async def test_transient_retry_and_provider_outage(self):
        provider = ScriptedLLM([RuntimeError("offline"), plan_response({"action": "greet"})])
        agent = await self.world.agent(provider)
        self.assertIn("Hello", (await agent.respond("Hi")).text)
        self.assertEqual(len(provider.calls), 2)
        provider = ScriptedLLM([RuntimeError("offline") for _ in range(3)])
        agent = await self.world.agent(provider)
        self.assertEqual((await agent.respond("Hi")).guardrail, "llm_unavailable")
        self.assertEqual(len(provider.calls), 3)

    async def test_model_text_without_plan_is_rejected(self):
        agent = await self.world.agent(ScriptedLLM([text_response("Unverified booking.")]))
        reply = await agent.respond("Book something.")
        self.assertEqual(reply.guardrail, "invalid_plan")
        self.assertFalse(self.world.mcp.calls)

class ProviderQuirkTests(unittest.TestCase):
    def test_gemini_thought_signature_is_echoed_back_on_tool_calls(self):
        from clinic_agent.llm import to_openai_messages
        sig = {"google": {"thought_signature": "abc123"}}
        msgs = [{"role": "user", "content": "hi"},
                {"role": "assistant", "content": [{"type": "tool_use", "id": "c1", "name": "get_slot",
                                                   "input": {"slot_id": "X"}, "extra_content": sig}]}]
        out = to_openai_messages("sys", msgs)
        self.assertEqual(out[2]["tool_calls"][0]["extra_content"], sig)

    def test_tool_calls_without_signature_are_unchanged(self):
        from clinic_agent.llm import to_openai_messages
        msgs = [{"role": "user", "content": "hi"},
                {"role": "assistant", "content": [{"type": "tool_use", "id": "c1", "name": "x", "input": {}}]}]
        self.assertNotIn("extra_content", to_openai_messages("s", msgs)[2]["tool_calls"][0])

    def test_server_retry_hint_is_parsed_from_gemini_style_errors(self):
        from clinic_agent.agent import ClinicAgent
        err = Exception('quota exceeded ... "retryDelay": "32s" ...')
        self.assertEqual(ClinicAgent._server_retry_hint(err), 32.0)
        self.assertEqual(ClinicAgent._server_retry_hint(Exception("Please retry in 7.5s.")), 7.5)
        self.assertIsNone(ClinicAgent._server_retry_hint(Exception("boom")))


class SchemaCleaningTests(unittest.TestCase):
    def test_optional_types_and_noise_are_simplified_for_strict_providers(self):
        from clinic_agent.llm import clean_schema
        raw = {"title": "X", "type": "object", "properties": {
            "department": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None, "title": "Department"},
            "days": {"anyOf": [{"type": "array", "items": {"type": "string"}}, {"type": "null"}], "default": None}},
            "required": [], "additionalProperties": False}
        out = clean_schema(raw)
        self.assertEqual(out["properties"]["department"], {"type": "string"})
        self.assertEqual(out["properties"]["days"]["type"], "array")
        self.assertNotIn("title", out)
        self.assertNotIn("additionalProperties", out)


if __name__ == "__main__":
    unittest.main()
