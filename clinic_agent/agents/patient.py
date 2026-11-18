from __future__ import annotations

from .base import Agent, AgentMessage

class PatientAgent(Agent):
    role = "patient"

    def __init__(self, patient: dict):
        super().__init__(patient["patient_id"], patient["name"], {
            "is_new": bool(patient.get("is_new")),
            "kind": "human" if patient.get("kind", "human") == "human" else "synthetic",
        })
        self.intentions = ["keep a convenient appointment"]

    def perceive_utterance(self, text: str) -> None:
        self.beliefs["last_utterance"] = text[:240]
        self.state = "requesting"

    def request_slot(self, department):
        self.intentions = [f"Book {department}"]
        self.beliefs["desired_department"] = department
        self.state = "requesting"
        self.last_act = "Requested a slot"
        self.messages_handled += 1
        return AgentMessage(self.agent_id, "scheduler", "REQUEST", {"department": department})

    def select_slot(self, proposal):
        self.state = "selecting"
        self.last_act = "Selected a slot"
        self.messages_handled += 1
        return AgentMessage(self.agent_id, "receptionist", "REQUEST", {"slot_id": proposal.content["slot_id"]},
                            in_reply_to=proposal.id)
