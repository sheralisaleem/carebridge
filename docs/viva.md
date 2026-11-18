# Viva walkthrough

Use separate browser profiles for the patient and doctor so both accounts can stay signed in.

1. **Create a doctor.** Sign up with an email and password, then open **Availability**. Add a weekly range or one-off slot in General Medicine so a new patient can book.
2. **Book as a patient.** Find the doctor under **Doctors**, choose the time, and review the approval dialog. Decline once to show oversight, then approve. Refresh the doctor’s agenda.
3. **Change the hours.** Remove that weekly range. The booked visit stays and is marked outside current hours. Run a new patient search to see that open slots changed.
4. **Show delegation.** Ask the Assistant to find a visit or look up the cancellation policy. Follow the live status and the ordered steps. Click an agent to inspect its role, source excerpt, and file path. Expand **Technical details** to show the plan, messages, MCP results, or evidence. Open **Agents** and select an activity metric to compare the agents.
5. **Show adaptation.** Arm a slot conflict from **Simulation** and try booking. Front Desk reports the conflict and Scheduler finds alternatives. Simulate doctor leave for a separate confirmed visit, then ask the Assistant to check the diary. Finish with “I have chest pain” to show the Safety Officer answering before the planner.

| If asked | Open |
|---|---|
| Where does model control stop? | `clinic_agent/agents/coordinator.py` → `respond`, `_dispatch`, `_recover` |
| What can the model propose? | `clinic_agent/agents/planning.py` → `TurnPlan.parse` |
| Which agent searches or books? | `clinic_agent/agents/scheduler.py`, `clinic_agent/agents/front_desk.py` → `execute` |
| How do doctor hours become slots? | `clinic_agent/doctor_schedule.py` → `set_weekly_hours`, `set_slot`, `_sync` |
| What keeps a booking simultaneous with an edit consistent? | `clinic_agent/clinic_tools.py` → `book_appointment`, and `tests/test_doctor_schedule.py` |
| How are accounts isolated? | `clinic_agent/auth.py`, `clinic_agent/web.py` → `require`, `ws_endpoint` |
| Where does activity come from? | `clinic_agent/agents/coordinator.py` → `_dispatch`, `clinic_agent/web_session.py` → `_on_trace` |
| What creates competition in the model? | `clinic_agent/simulation/runner.py` → `run_day` |
