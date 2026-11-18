from __future__ import annotations

from datetime import datetime
from random import Random
from typing import Callable

from ..clinic_db import apply_doctor_leave_for_patient, connect, list_patients
from ..doctor_schedule import weekly_hours
from ..agents.base import Agent, AgentMessage
from .environment import ClinicEnvironment
from ..agents.patient import PatientAgent
from ..agents.coordinator import CoordinatorAgent
from ..agents.safety_officer import SafetyOfficerAgent
from ..agents.scheduler import SchedulerAgent
from ..agents.front_desk import ReceptionistAgent
from ..agents.policy import PolicyAgent
from ..agents.memory_keeper import MemoryKeeperAgent
from ..agents.doctor import DoctorAgent

class ClinicSociety:
    """The multi-agent system: register, dispatch speech acts, snapshot, simulate."""

    def __init__(self, env: ClinicEnvironment, detect_emergency: Callable[[str], bool]):
        self.env = env
        self.transcript: list[AgentMessage] = []
        self.agents: dict[str, Agent] = {}
        for a in (
            CoordinatorAgent(),
            SafetyOfficerAgent(detect_emergency),
            SchedulerAgent(),
            ReceptionistAgent(),
            PolicyAgent(),
            MemoryKeeperAgent(),
        ):
            self.register(a)
        self.refresh_doctors()

    def refresh_doctors(self):
        with connect(self.env.db_path) as conn:
            for row in conn.execute("SELECT * FROM doctors").fetchall():
                did = row["doctor_id"]
                hours = weekly_hours(conn, did)
                agent = self.agents.get(did)
                if agent is None:
                    agent = DoctorAgent(did, row["name"], row["department"], row["languages"],
                                        row["telehealth"], row["requires_referral"], [])
                    self.register(agent)
                agent.attributes["working_weekdays"] = sorted({hour["weekday"] for hour in hours})

    def register(self, agent: Agent) -> None:
        self.agents[agent.agent_id] = agent

    def get(self, agent_id: str) -> Agent:
        return self.agents[agent_id]

    def attach_patient(self, patient: dict) -> PatientAgent:
        # Drop previous human patient (one signed-in user per web session society clone
        # is fine; the runtime society keeps the latest).
        for aid, ag in list(self.agents.items()):
            if ag.role == "patient" and ag.attributes.get("kind") == "human":
                del self.agents[aid]
        pa = PatientAgent({**patient, "kind": "human"})
        self.register(pa)
        return pa

    def dispatch(self, msg: AgentMessage) -> None:
        msg.tick = self.env.tick
        self.transcript.append(msg)
        if len(self.transcript) > 600:
            del self.transcript[:-600]
        self.env.metrics["messages"] += 1
        for agent_id in {msg.sender, msg.receiver}:
            if agent := self.agents.get(agent_id):
                agent.messages_exchanged += 1
        target = self.agents.get(msg.receiver)
        if target:
            target.inbox.append(msg)
            if len(target.inbox) > 100:
                del target.inbox[:-100]

    def screen(self, text: str) -> AgentMessage:
        patient = next((a for a in self.agents.values() if a.role == "patient"), None)
        sender = patient.agent_id if patient else "patient"
        if patient:
            patient.perceive_utterance(text)
        query = AgentMessage(sender=sender, receiver="safety", performative="QUERY",
                             content={"event": "screen_request"})
        self.dispatch(query)
        verdict = self.get("safety").screen(text)  # type: ignore[attr-defined]
        verdict.in_reply_to = query.id
        self.dispatch(verdict)
        return verdict

    def record_plan(self, goal: str, is_replan: bool) -> None:
        self.get("coordinator").record_plan(goal, is_replan)  # type: ignore[attr-defined]
        if is_replan:
            self.env.metrics["replans"] += 1
        self.dispatch(AgentMessage(
            sender="coordinator", receiver="coordinator",
            performative="INFORM",
            content={"event": "replan" if is_replan else "plan", "goal": goal},
        ))

    def note_booking(self, ok: bool, conflict: bool = False) -> None:
        if ok:
            self.env.metrics["bookings"] += 1
        else:
            self.env.metrics["refusals"] += 1
        if conflict:
            self.env.metrics["conflicts"] += 1

    def apply_doctor_leave(self, patient_id: str) -> dict | None:
        result = apply_doctor_leave_for_patient(self.env.db_path, patient_id)
        if not result:
            return None
        self.refresh_doctors()
        doctor_id = None
        for a in self.agents.values():
            if a.role == "doctor" and a.agent_id == result.get("doctor_id"):
                doctor_id = a.agent_id
                msg = a.go_on_leave(result["date"])  # type: ignore[attr-defined]
                self.dispatch(msg)
                break
        self.env.metrics["cancellations"] += result["appointments_cancelled"]
        self.get("receptionist").messages_handled += 1
        if doctor_id:
            result = {**result, "doctor_id": doctor_id}
        return result

    def recent_messages(self, n: int = 40) -> list[dict]:
        return [m.snapshot() for m in self.transcript[-n:]]

    def snapshot(self) -> dict:
        by_role: dict[str, list[dict]] = {}
        for a in self.agents.values():
            by_role.setdefault(a.role, []).append(a.snapshot())
        return {
            "environment": self.env.snapshot(),
            "agents": [a.snapshot() for a in self.agents.values()],
            "by_role": {k: len(v) for k, v in by_role.items()},
            "messages": self.recent_messages(50),
            "roles": [
                {"role": "patient", "count": self.env_count("patient"),
                 "job": "Holds goals and preferences; originates REQUEST acts."},
                {"role": "coordinator", "count": 1,
                 "job": "LLM planner. Delegates work; replans when the world changes."},
                {"role": "safety", "count": 1,
                 "job": "Code-level guardrails. Can refuse before the LLM runs."},
                {"role": "scheduler", "count": 1,
                 "job": "Searches the slot environment through MCP."},
                {"role": "receptionist", "count": 1,
                 "job": "Mutates the diary only after a human CONFIRM."},
                {"role": "policy", "count": 1,
                 "job": "Retrieves grounded clinic-policy passages (RAG)."},
                {"role": "memory", "count": 1,
                 "job": "Persists non-clinical preferences across sessions."},
                {"role": "doctor", "count": self.env_count("doctor"),
                 "job": "Autonomous clinicians. Broadcast leave; occupy slots."},
            ],
        }

    def env_count(self, role: str) -> int:
        return sum(1 for a in self.agents.values() if a.role == role)

    # ----- discrete-time ABM demo (no LLM) ---------------------------------
    def seed_synthetic_patients(self, n: int = 8) -> None:
        existing = {a.agent_id for a in self.agents.values() if a.role == "patient"}
        for i in range(1, n + 1):
            pid = f"SIM-{i:02d}"
            if pid in existing:
                continue
            self.register(PatientAgent({
                "patient_id": pid, "name": f"Simulated Patient {i}",
                "is_new": i % 3 == 0, "kind": "synthetic",
            }))

    def run_day(self, ticks: int = 12, rng: Random | None = None, extra_patients: int = 8) -> dict:
        from .runner import run_day

        return run_day(self, ticks, rng or Random(2026), extra_patients)

    def availability_changed(self, doctor_id, date_value=None):
        self.refresh_doctors()
        self.dispatch(self.agents[doctor_id].availability_changed(date_value))


def build_society(db_path, now: datetime, detect_emergency: Callable[[str], bool],
                  patients: list[dict] | None = None) -> ClinicSociety:
    society = ClinicSociety(ClinicEnvironment(db_path, now), detect_emergency)
    for p in list_patients(db_path) if patients is None else patients:
        if p["patient_id"] not in society.agents:
            society.register(PatientAgent({**p, "kind": "registered"}))
    return society


def registered_patients(db_path) -> list[dict]:
    return list_patients(db_path)
