from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any
PERFORMATIVES = ("REQUEST", "QUERY", "INFORM", "PROPOSE", "CONFIRM", "REFUSE", "DELEGATE")

def _mid() -> str:
    return uuid.uuid4().hex[:10]


@dataclass
class AgentMessage:
    """One speech act on the clinic message bus."""
    sender: str
    receiver: str
    performative: str
    content: dict
    id: str = field(default_factory=_mid)
    in_reply_to: str | None = None
    tick: int = 0

    def snapshot(self) -> dict:
        return {
            "id": self.id, "from": self.sender, "to": self.receiver,
            "performative": self.performative, "content": self.content,
            "in_reply_to": self.in_reply_to, "tick": self.tick,
        }


class Agent:
    """Base BDI-ish agent: identity, attributes, beliefs, mailbox, act()."""

    role = "agent"

    def __init__(self, agent_id: str, name: str, attributes: dict | None = None):
        self.agent_id = agent_id
        self.name = name
        self.attributes: dict[str, Any] = dict(attributes or {})
        self.beliefs: dict[str, Any] = {}
        self.intentions: list[str] = []
        self.state = "idle"
        self.messages_handled = 0
        self.messages_exchanged = 0
        self.last_act: str | None = None
        self.inbox: list[AgentMessage] = []

    def snapshot(self) -> dict:
        return {
            "id": self.agent_id, "name": self.name, "role": self.role,
            "state": self.state, "attributes": self.attributes,
            "beliefs": {k: v for k, v in self.beliefs.items() if _jsonish(v)},
            "intentions": list(self.intentions),
            "messages_handled": self.messages_handled,
            "last_act": self.last_act,
        }


def _jsonish(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool, type(None), list, dict))
