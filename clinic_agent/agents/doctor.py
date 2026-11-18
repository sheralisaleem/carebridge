from __future__ import annotations

from .base import Agent, AgentMessage

class DoctorAgent(Agent):
    role = "doctor"

    def __init__(self, doctor_id: str, name: str, department: str, languages: str,
                 telehealth: int, requires_referral: int, weekdays: set[int]):
        super().__init__(doctor_id, name, {
            "department": department, "languages": languages,
            "telehealth": bool(telehealth), "requires_referral": bool(requires_referral),
            "working_weekdays": sorted(weekdays),
        })
        self.beliefs["on_leave"] = False
        self.intentions = ["keep published clinic hours"]

    def go_on_leave(self, day: str) -> AgentMessage:
        self.beliefs["on_leave"] = True
        self.beliefs["leave_date"] = day
        self.state = "unavailable"
        self.last_act = "INFORM"
        self.messages_handled += 1
        return AgentMessage(
            sender=self.agent_id, receiver="receptionist", performative="INFORM",
            content={"event": "doctor_leave", "date": day, "doctor_id": self.agent_id},
        )

    def availability_changed(self, date_value=None):
        self.last_act = "Published availability"
        self.state = "completed"
        self.messages_handled += 1
        return AgentMessage(self.agent_id, "scheduler", "INFORM",
                            {"event": "availability_updated", "date": date_value})
