"""Session orchestration and provider retry for the CareBridge Coordinator."""
from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from .base import Agent, AgentMessage
from .planning import PLAN_TOOL, WRITES, Task, TurnPlan
from .scheduler import SchedulerAgent
from .front_desk import ReceptionistAgent
from .policy import PolicyAgent
from .memory_keeper import MemoryKeeperAgent
from .safety_officer import SafetyOfficerAgent
from ..llm import ConfigError
from ..memory import ShortTermMemory
from ..prompts import planning_prompt
from ..safety import EMERGENCY_REPLY, detect_emergency, flag_injection
from ..tracing import Tracer

ConfirmFn = Callable[[str], Awaitable[bool]]
CLARIFY = "What would you like to do? I can find a visit, manage your appointments, or look up clinic information."
BOOKING_REQUEST = re.compile(r"\b(?:book|schedule|make|set up)\b[^.!?\n]*\b(?:appointment|visit)\b", re.I)
REFUSE = "I can help with clinic policies and your own appointments. For medical advice, please speak with a clinician."
UNAVAILABLE = "The planning service is temporarily unavailable. Try again shortly or call the front desk at 555-0142."
TASK_ACTIVITY = {
    "find_slots": "Checking the clinic schedule for matching times",
    "list_doctors": "Looking up doctors in the clinic directory",
    "doctor_hours": "Checking the doctor's published hours",
    "list_appointments": "Reading your appointment diary",
    "book": "Verifying the selected time",
    "cancel": "Verifying the appointment to cancel",
    "reschedule": "Checking the appointment and replacement time",
    "policy": "Looking up evidence in clinic documents",
    "remember": "Saving your scheduling preference",
    "forget": "Removing your saved preference",
}


@dataclass
class AgentReply:
    text: str
    steps: int = 0
    tool_calls: list[dict] = field(default_factory=list)
    plan_version: int = 0
    guardrail: str | None = None
    slots: list[dict] = field(default_factory=list)
    reschedule_id: str | None = None
    appointments: list[dict] = field(default_factory=list)
    appointment_action: str | None = None


class CoordinatorAgent(Agent):
    role = "coordinator"

    def __init__(self):
        super().__init__("coordinator", "CareBridge Coordinator", {
            "uses_llm": True, "max_steps": 8, "job": "plan, delegate, replan",
        })
        self.intentions = ["satisfy the patient's request without breaking clinic rules"]

    def record_plan(self, goal: str, is_replan: bool) -> None:
        self.beliefs["goal"] = goal
        self.beliefs["replanning"] = is_replan
        self.state = "replanning" if is_replan else "planning"
        self.last_act = "REPLAN" if is_replan else "PLAN"
        self.messages_handled += 1


class ClinicAgent:
    def __init__(self, *, llm, mcp, kb, ltm, patient, clinic_name, clinic_now, confirm=None, tracer=None,
                 session_id=None, stm_turns=12, max_tokens=1500, llm_retries=2, retry_delay=1,
                 min_call_interval=0, society=None):
        self.llm, self.mcp, self.kb, self.ltm = llm, mcp, kb, ltm
        self.patient_id = patient["patient_id"]
        self.patient = patient
        self.clinic_name, self.clinic_now, self.confirm = clinic_name, clinic_now, confirm
        self.session_id = session_id or f"s-{uuid.uuid4().hex[:8]}"
        self.tracer = tracer or Tracer(self.session_id, echo=False)
        self.stm = ShortTermMemory(max_turns=stm_turns)
        self.max_tokens = max_tokens
        self.llm_retries, self.retry_delay, self.min_call_interval = llm_retries, retry_delay, min_call_interval
        self._last_call = 0.0
        self.society = society
        self.agents = society.agents if society else {a.agent_id: a for a in (
            CoordinatorAgent(), SchedulerAgent(), ReceptionistAgent(), PolicyAgent(), MemoryKeeperAgent(),
            SafetyOfficerAgent(detect_emergency),
        )}
        self.plan = None
        self.plan_history = []
        self.replans = 0
        self.saved_facts = {}
        self.slots = []
        self.slot_filters = {}
        self.reschedule_id = None
        self.turn = ""
        self.task_id = None
        self.user_text = ""
        self.explicit = False
        self.tool_log = []
        self._reported_cancellations = set()

    async def start(self):
        self.emit("info", text="Patient session started")

    def emit(self, kind, **fields):
        self.tracer.emit(kind, turn_id=self.turn, task_id=self.task_id, **fields)

    def _message(self, message):
        if self.society:
            self.society.dispatch(message)
        self.emit("agent_message", **message.snapshot())

    def _status(self, agent_id, state, detail):
        agent = self.agents[agent_id]
        agent.state, agent.last_act = state, detail
        self.emit("agent_status", agent=agent_id, state=state, detail=detail)

    def waiting(self, agent_id):
        self._status(agent_id, "waiting", "Waiting for your approval")

    def applying(self, agent_id):
        self._status(agent_id, "working", "Sending the approved change to the scheduling server")

    async def mcp_call(self, name, args):
        self.emit("tool_call", name=name, args={key: value for key, value in args.items() if key != "patient_id"},
                  via="MCP")
        try:
            response = await self.mcp.call_tool(name, args)
            result = response.data
            if not isinstance(result, dict) or "ok" not in result:
                result = {"ok": False, "error_code": "INVALID_RESULT", "message": "The scheduler response could not be verified."}
        except Exception:
            result = {"ok": False, "error_code": "MCP_ERROR", "message": "The scheduling service is unavailable."}
        self.emit("tool_result", name=name, result=result)
        self.tool_log.append({"name": name, "ok": result["ok"]})
        return result

    def preferences(self):
        # Canonical keys for new saves and aliases used by existing demo memory.
        facts = {fact["key"]: fact["value"] for fact in self.ltm.facts(self.patient_id)}
        out = {}
        for key in ("part_of_day", "preferred_time", "time_of_day", "preferred_time_of_day"):
            value = facts.get(key, "").casefold()
            if value in ("morning", "mornings", "afternoon", "afternoons"):
                out["part_of_day"] = value.rstrip("s")
                break
        for key in ("exclude_weekdays", "avoid_days", "unavailable_days", "avoid_weekdays"):
            days = re.findall(r"\b(mon|tue|wed|thu|fri|sat|sun)\w*\b", facts.get(key, ""), re.I)
            if days:
                out["exclude_weekdays"] = [day.title() for day in days]
                break
        return out

    def accepts_slot(self, slot_id):
        return self.explicit or slot_id.casefold() in self.user_text.casefold() or any(
            item["slot_id"] == slot_id for item in self.slots
        )

    def _begin(self, text, explicit=False):
        self.turn = uuid.uuid4().hex[:12]
        self.task_id, self.user_text, self.explicit, self.tool_log = None, text, explicit, []
        self.stm.begin_turn({"role": "user", "content": text})
        self.emit("user", text=text)

    async def respond(self, text):
        self._begin(text)
        if self._screen(text):
            return self._finish(EMERGENCY_REPLY, guardrail="emergency")
        self._status("coordinator", "working", "Interpreting the request")
        try:
            response = await self._call_llm()
        except Exception as exc:
            self.emit("tool_error", name="planner", error=type(exc).__name__)
            self._status("coordinator", "failed", "Planning service unavailable")
            return self._finish(UNAVAILABLE, guardrail="llm_unavailable")
        try:
            if len(response.tool_calls) != 1 or response.tool_calls[0].name != PLAN_TOOL["name"]:
                raise ValueError("Expected one plan")
            plan = TurnPlan.parse(response.tool_calls[0].input)
        except (TypeError, ValueError):
            self._status("coordinator", "failed", "Invalid task plan")
            self.emit("guardrail", rule="invalid_plan", detail="The model response did not match the plan contract.")
            return self._finish(self._clarification(), steps=1, guardrail="invalid_plan")
        self._status("coordinator", "completed", "Plan ready")
        return await self._run(plan, steps=1)

    async def respond_action(self, raw):
        self._begin("Selected an action in the portal.", explicit=True)
        try:
            task = Task.parse(raw)
        except ValueError:
            return self._finish(CLARIFY, guardrail="invalid_task")
        return await self._run(TurnPlan(task.label, (task,)), steps=0)

    def _screen(self, text):
        self._status("safety", "working", "Screening the message")
        patient = self.agents.get(self.patient_id)
        if patient:
            patient.perceive_utterance(text)
        query = AgentMessage(self.patient_id, "safety", "QUERY", {"event": "screen_request"})
        self._message(query)
        verdict = self.agents["safety"].screen(text)
        verdict.in_reply_to = query.id
        self._message(verdict)
        hit = verdict.performative == "REFUSE"
        self._status("safety", "refused" if hit else "completed", "Safety message returned" if hit else "Screened request")
        if hit:
            self.emit("guardrail", rule="emergency_prescreen", detail="Safety Officer answered before planning.")
        return hit

    async def _call_llm(self):
        messages = self.stm.messages[:-1]
        messages = [
            {"role": item["role"], "content": item["content"]}
            for item in messages if isinstance(item["content"], str)
        ]
        context = {
            "new_patient": bool(self.patient.get("is_new")),
            "saved_scheduling_preferences": self.preferences(),
            "saved_preference_keys": [fact["key"] for fact in self.ltm.facts(self.patient_id)],
            "displayed_options": [{**item, "option": index} for index, item in enumerate(self.slots, 1)],
            "reschedule_selection": self.reschedule_id,
        }
        messages.append({"role": "user", "content": f"Request: {self.user_text}\nContext data: {json.dumps(context)}"})
        for attempt in range(self.llm_retries + 1):
            try:
                gap = self.min_call_interval - (time.monotonic() - self._last_call)
                if gap > 0:
                    await asyncio.sleep(gap)
                self._last_call = time.monotonic()
                return await self.llm.chat(system=planning_prompt(self.clinic_name, self.clinic_now),
                                           messages=messages, tools=[PLAN_TOOL], max_tokens=self.max_tokens)
            except Exception as exc:
                code = getattr(exc, "status_code", None)
                if isinstance(exc, ConfigError) or (isinstance(code, int) and 400 <= code < 500 and code != 429):
                    raise
                if attempt >= self.llm_retries:
                    raise
                hint = self._server_retry_hint(exc)
                await asyncio.sleep(min(15, max(self.retry_delay * 2**attempt, hint or 0)))

    @staticmethod
    def _server_retry_hint(exc):
        match = re.search(r'(?:(?:"retryDelay"\s*:\s*")|(?:retry in\s+))(\d+(?:\.\d+)?)s', str(exc), re.I)
        if match:
            return float(match.group(1))
        response = getattr(exc, "response", None)
        try:
            return float(response.headers.get("retry-after")) if response is not None else None
        except (TypeError, ValueError):
            return None

    def _record(self, plan, reason=None):
        self.plan = {
            "version": len(self.plan_history) + 1, "goal": plan.goal,
            "steps": [{"step": task.label, "tool": task.owner} for task in plan.tasks],
            "is_replan": bool(reason), "reason": reason or "",
        }
        self.plan_history.append(deepcopy(self.plan))
        if reason:
            self.replans += 1
        if self.society:
            self.society.record_plan(plan.goal, bool(reason))
        self.emit("replan" if reason else "plan", **self.plan)

    async def _dispatch(self, task):
        if task.owner == "coordinator":
            if task.action == "start_booking":
                return {"ok": True, "message": self._booking_question()}
            return {"ok": True, "message": {
                "greet": "Hello. I can help you find a clinic visit or manage an appointment.",
                "clarify": self._clarification(), "refuse": REFUSE,
            }[task.action]}
        request = AgentMessage("coordinator", task.owner, "DELEGATE", {"task": task.action})
        last_task = self.task_id
        self.task_id = request.id
        self._message(request)
        self._status(task.owner, "working", TASK_ACTIVITY[task.action])
        agent = self.agents[task.owner]
        try:
            result = await agent.execute(task, self)
        except asyncio.CancelledError:
            self._status(task.owner, "refused", "Request ended")
            raise
        except Exception as exc:
            self.emit("tool_error", name=task.action, error=type(exc).__name__)
            result = {"ok": False, "message": "This task could not be completed. Try again shortly."}
        ok = result.get("ok", True)
        agent.messages_handled += 1
        outcome = self._describe_outcome(task, result)
        self._message(AgentMessage(task.owner, "coordinator", "INFORM" if ok else "REFUSE",
                                   {"task": task.action, "outcome": outcome}, in_reply_to=request.id))
        terminal = "completed" if ok else "refused" if result.get("error_code") == "USER_DECLINED" else "failed"
        self._status(task.owner, terminal, outcome)
        self.task_id = last_task
        return result

    def _clarification(self):
        return self._booking_question() if BOOKING_REQUEST.search(self.user_text) else CLARIFY

    def _booking_question(self):
        if self.patient.get("is_new"):
            return "Your first visit is with General Medicine. What day or time works for you?"
        return "Which doctor or department would you like to book with?"

    @staticmethod
    def _describe_outcome(task, result):
        if not result.get("ok", True):
            return result.get("message") or result.get("error") or "The task could not be completed"
        if "slots" in result:
            count = len(result["slots"])
            return f"Found {count} available time{'s' if count != 1 else ''}"
        if task.action == "policy":
            count = len(result.get("passages", []))
            return f"Retrieved {count} evidence passage{'s' if count != 1 else ''}" if count else "No supporting evidence found"
        if task.action == "list_appointments":
            return "Appointment diary checked"
        if task.action == "remember":
            return "Preference saved"
        if task.action == "forget":
            return "Saved preferences checked"
        return result.get("message") or task.label

    async def _run(self, plan, steps):
        self._record(plan)
        fragments, choices, appointments = [], [], []
        appointment_action = None
        for task in plan.tasks:
            if task.action == "find_slots":
                self.reschedule_id = None
            if task.action == "book" and self.reschedule_id and not self.explicit:
                task = Task("reschedule", {**task.fields, "appointment_id": self.reschedule_id})
            result = await self._dispatch(task)
            fragments.append(self._format(task, result))
            if "appointments" in result:
                appointments = result["appointments"]
                appointment_action = result.get("appointment_action")
            if "slots" in result:
                choices = result["slots"]
                self.slots = choices
            if task.action in WRITES and result.get("ok"):
                self.slots, self.reschedule_id, choices = [], None, []
            if task.action in {"book", "reschedule"} and result.get("error_code") == "SLOT_UNAVAILABLE":
                choices = await self._recover("The selected time is no longer available.",
                                              result.get("slot", {}), self.slot_filters)
                if task.action == "reschedule":
                    self.reschedule_id = result.get("reschedule_id") or task.fields.get("appointment_id") or self.reschedule_id
                fragments.append(self._slots_text(choices))
            if task.action in {"reschedule", "list_appointments"}:
                next_visit = self._replacement_visit(task, result)
                if next_visit:
                    self.reschedule_id = next_visit["appointment_id"]
                    reason = ("The clinic cancelled the appointment."
                              if next_visit["status"] == "cancelled_by_clinic" else None)
                    choices = await self._recover(reason, next_visit, {"department": next_visit["department"]})
                    fragments.append(self._slots_text(choices))
            if result.get("error_code") == "USER_DECLINED":
                break
        return self._finish("\n\n".join(filter(None, fragments)), steps=steps, choices=choices,
                            appointments=appointments, appointment_action=appointment_action)

    def _replacement_visit(self, task, result):
        candidates = result.get("appointments", [])
        if task.action == "reschedule" and not (task.fields.get("option") or task.fields.get("slot_id")):
            return candidates[0] if len(candidates) == 1 else None
        if task.action == "list_appointments":
            for item in candidates:
                aid = item["appointment_id"]
                if item["status"] == "cancelled_by_clinic" and aid not in self._reported_cancellations:
                    self._reported_cancellations.add(aid)
                    return item
        return None

    async def _recover(self, reason, slot, filters):
        args = {key: value for key, value in filters.items() if key != "limit"}
        if not args.get("department") and slot.get("department"):
            args["department"] = slot["department"]
        task = Task("find_slots", args)
        if reason:
            self._record(TurnPlan("Find available alternatives", (task,)), reason)
        result = await self._dispatch(task)
        self.slots = result.get("slots", [])
        return self.slots

    @staticmethod
    def _slots_text(slots):
        if not slots:
            return "I found no open times that match. Try another date or call the front desk at 555-0142."
        return "Choose an available time:\n" + "\n".join(
            f"{i}. {ReceptionistAgent.describe(slot)}" for i, slot in enumerate(slots, 1)
        )

    def _format(self, task, result):
        if result.get("ok") is False:
            return result.get("message") or result.get("error", "This request could not be completed.")
        if "slots" in result:
            return self._slots_text(result["slots"])
        if "doctors" in result:
            if not result["doctors"]:
                return "No doctors matched that search."
            lines = []
            for doc in result["doctors"]:
                text = f"{doc['name']} · {doc['department']}"
                if task.action == "doctor_hours":
                    days = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
                    text += ": " + (", ".join(
                        f"{days[hour['weekday']]} {hour['start_time']}–{hour['end_time']}" for hour in doc.get("weekly_hours", [])
                    ) or "No weekly hours published.")
                lines.append(text)
            return "\n".join(lines)
        if task.action == "policy":
            if not result["found"]:
                return "I couldn't find that in the clinic documents. You can call the front desk at 555-0142."
            pieces = []
            for passage in result["passages"][:2]:
                text = passage["text"].strip()
                pieces.append(f"{text}\nSource: {passage['source']} · {passage['section']}")
            return "\n\n".join(pieces)
        if task.action == "list_appointments":
            if not result["appointments"]:
                return "You have no appointments in this view."
            return "\n".join(
                f"{item['appointment_id']} · {ReceptionistAgent.describe(item)} · "
                f"{item['status'].replace('_', ' ')}"
                for item in result["appointments"]
            )
        if task.action == "remember":
            return f"Saved your preference for {result['saved']['value']}."
        if task.action == "forget":
            return "Removed the saved preference." if result["deleted"] else "I found no matching preference."
        if result.get("appointment"):
            visit = result["appointment"]
            return f"{result['message']} {ReceptionistAgent.describe(visit)}. Reference {visit['appointment_id']}."
        if result.get("late_cancellation"):
            return "Your appointment was cancelled. A late-cancellation fee may apply."
        return result.get("message", "")

    def _finish(self, text, steps=0, guardrail=None, choices=None, appointments=None, appointment_action=None):
        self.stm.add({"role": "assistant", "content": text if not flag_injection(text) else "Clinic information was returned."})
        self.emit("assistant", text=text)
        return AgentReply(text, steps, self.tool_log, len(self.plan_history), guardrail, choices or [],
                          self.reschedule_id, appointments or [], appointment_action)

    async def diary(self, include_past=False):
        return await self.agents["receptionist"].diary(self, include_past)

    def end_session(self):
        return self.agents["memory"].summarize(self)
