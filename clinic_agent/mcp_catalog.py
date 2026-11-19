"""Documentation for the scheduling tools exposed by the MCP server."""

import ast
from functools import lru_cache

from .config import ROOT

TOOLS = {
    "list_doctors": (
        "Scheduler", "Find clinicians and weekly hours",
        "The Scheduler calls this for directory or hours requests and when a slot search names a doctor. It can match the name to a doctor ID before searching availability.",
        "Optional department. Returns clinician IDs, names, departments, weekly hours and availability metadata.",
    ),
    "find_available_slots": (
        "Scheduler", "Search for open appointment times",
        "The Scheduler combines saved preferences with the current request and caps the returned options at three. It calls the same tool when a selected time is lost and alternatives are needed.",
        "Accepts doctor or department, dates, time of day, excluded weekdays, one-per-day and a result limit. Returns matching slot IDs and visit details.",
    ),
    "get_slot": (
        "Front Desk", "Check a selected slot before asking for approval",
        "Front Desk resolves the selected option and checks its current state. The returned details populate the approval card. A slot that is already unavailable can trigger a new search.",
        "Requires a slot ID. Returns the slot, clinician, date, time and status, or an error for an unknown slot.",
    ),
    "list_my_appointments": (
        "Front Desk and web session", "Read the signed-in patient's diary",
        "Front Desk uses this to resolve a cancellation or reschedule target. The web session also uses it to refresh the portal. The patient ID comes from the authenticated session.",
        "Requires a patient ID and accepts an include-past flag. Returns the patient's appointments and their statuses.",
    ),
    "book_appointment": (
        "Front Desk", "Book an approved appointment",
        "After approval, Front Desk asks the service to reserve the selected slot. The SQLite transaction checks availability again and applies clinic booking rules before creating the appointment.",
        "Requires patient ID and slot ID. Accepts an optional reason. Returns a booking reference and confirmation, or a structured error such as SLOT_UNAVAILABLE.",
    ),
    "cancel_appointment": (
        "Front Desk", "Cancel an approved appointment",
        "Front Desk checks who owns the visit and asks for approval. The service checks the appointment state, cancels it and releases the slot where applicable.",
        "Requires patient ID and appointment ID. Returns cancellation status and whether the late-cancellation policy applies.",
    ),
    "reschedule_appointment": (
        "Front Desk", "Move an approved appointment to a new slot",
        "Front Desk presents the current visit and replacement time for approval. The service checks the new slot and updates both visits in one transaction. It also supports rebooking visits cancelled by the clinic.",
        "Requires patient ID, appointment ID and new slot ID. Returns the old reference and the new appointment, or a structured conflict or ownership error.",
    ),
}


@lru_cache(maxsize=1)
def mcp_catalog():
    """Read allowlisted tool source without starting or importing the server."""
    path = "clinic_agent/mcp_server.py"
    source = (ROOT / path).read_text(encoding="utf-8")
    definitions = {item.name: item for item in ast.parse(source).body if isinstance(item, ast.FunctionDef)}
    return [
        {"name": name, "owner": owner, "summary": summary, "usage": usage, "contract": contract,
         "mode": "write" if name in {"book_appointment", "cancel_appointment", "reschedule_appointment"} else "read",
         "source": f"{path}:{definitions[name].lineno}", "code": ast.get_source_segment(source, definitions[name])}
        for name, (owner, summary, usage, contract) in TOOLS.items()
    ]
