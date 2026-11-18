from __future__ import annotations

from typing import Callable

from .base import Agent, AgentMessage

class SafetyOfficerAgent(Agent):
    role = "safety"

    def __init__(self, detect_emergency: Callable[[str], bool]):
        super().__init__("safety", "Safety Officer", {"bypasses_llm_on_emergency": True})
        self._detect = detect_emergency
        self.intentions = ["block emergencies and identity spoofing"]

    def screen(self, text: str) -> AgentMessage:
        hit = bool(self._detect(text))
        self.beliefs["last_screen"] = "emergency" if hit else "clear"
        self.messages_handled += 1
        self.last_act = "REFUSE" if hit else "CONFIRM"
        self.state = "idle"
        return AgentMessage(
            sender=self.agent_id, receiver="coordinator",
            performative="REFUSE" if hit else "CONFIRM",
            content={"rule": "emergency_prescreen", "triggered": hit},
        )
