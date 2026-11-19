"""Display metadata and allowlisted source excerpts for the agent inspector."""
from functools import lru_cache
from inspect import getsourcelines, getsourcefile
from pathlib import Path
from textwrap import dedent

from .agents.coordinator import ClinicAgent
from .agents.front_desk import ReceptionistAgent
from .agents.memory_keeper import MemoryKeeperAgent
from .agents.policy import PolicyAgent
from .agents.safety_officer import SafetyOfficerAgent
from .agents.scheduler import SchedulerAgent
from .config import ROOT

ROLES = {
    "coordinator": (
        "Coordinator", "flow", "Plan and delegate",
        "Gives the LLM one chance to submit a task plan. Python validates the plan, dispatches each task to its owner, and joins the results into a reply.",
        ClinicAgent._dispatch, "request = AgentMessage",
    ),
    "safety": (
        "Safety Officer", "shield", "Screen incoming messages",
        "Checks patient messages for emergency language before the Coordinator starts. An emergency triggers a fixed safety response and skips planning.",
        SafetyOfficerAgent.screen, None,
    ),
    "scheduler": (
        "Scheduler", "calendar", "Find matching times",
        "Combines current search criteria with saved preferences, checks availability through MCP, and returns up to three choices. It also finds alternatives during a replan.",
        SchedulerAgent.execute, "options = {**context.preferences()",
    ),
    "receptionist": (
        "Front Desk", "doctor", "Manage appointments",
        "Checks the signed-in patient's diary and prepares booking changes. The patient reviews the exact details before Front Desk sends a write to MCP.",
        ReceptionistAgent.execute, "context.waiting(",
    ),
    "policy": (
        "Policy Librarian", "book", "Retrieve policy evidence",
        "Searches clinic documents, records the passages and scores, and returns evidence for a cited answer. It reports when there is insufficient evidence.",
        PolicyAgent.execute, None,
    ),
    "memory": (
        "Memory Keeper", "memory", "Manage saved preferences",
        "Stores scheduling preferences in SQLite for the signed-in patient. The Scheduler can apply them in later conversations. Patients can remove their saved preferences.",
        MemoryKeeperAgent.save, None,
    ),
}


@lru_cache(maxsize=1)
def agent_catalog():
    catalog = {}
    for role, (title, icon, summary, explanation, method, anchor) in ROLES.items():
        lines, start = getsourcelines(method)
        if anchor:
            offset = next(index for index, line in enumerate(lines) if anchor in line)
            start += offset
            lines = lines[offset:]
        path = Path(getsourcefile(method)).relative_to(ROOT).as_posix()
        catalog[role] = {
            "title": title, "icon": icon, "summary": summary, "explanation": explanation,
            "symbol": method.__qualname__, "path": path, "line": start,
            "code": dedent("".join(lines[:13])).rstrip(),
        }
    return catalog


def agent_overview(society):
    """Return the dashboard roster and shared activity counters."""
    return {
        "agents": [
            {
                "id": agent.agent_id, "name": agent.name, "role": agent.role,
                "state": agent.state, "last": agent.last_act or "",
                "actions": agent.messages_handled, "messages": agent.messages_exchanged,
            }
            for agent in society.agents.values() if agent.role in ROLES
        ],
        "messages": society.env.metrics["messages"],
        "replans": society.env.metrics["replans"],
    }
