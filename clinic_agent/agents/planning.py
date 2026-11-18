"""The only interface the model receives. Execution uses allowlisted Python tasks."""
from dataclasses import dataclass
from datetime import date

from ..memory import ALLOWED_CATEGORIES

FIELDS = {
    "department": {"type": "string"},
    "doctor_name": {"type": "string"},
    "doctor_id": {"type": "string"},
    "date_from": {"type": "string"},
    "date_to": {"type": "string"},
    "part_of_day": {"type": "string", "enum": ["morning", "afternoon"]},
    "exclude_weekdays": {"type": "array", "items": {"type": "string"}},
    "one_per_day": {"type": "boolean"},
    "include_past": {"type": "boolean"},
    "appointment_id": {"type": "string"},
    "appointment_hint": {"type": "string"},
    "slot_id": {"type": "string"},
    "reason": {"type": "string"},
    "option": {"type": "integer", "minimum": 1, "maximum": 3},
    "query": {"type": "string"},
    "category": {"type": "string", "enum": list(ALLOWED_CATEGORIES)},
    "key": {"type": "string"},
    "value": {"type": "string"},
}
SPECS = {
    "find_slots": ("scheduler", "department doctor_name doctor_id date_from date_to part_of_day exclude_weekdays one_per_day"),
    "list_doctors": ("scheduler", "department doctor_name"),
    "doctor_hours": ("scheduler", "department doctor_name"),
    "list_appointments": ("receptionist", "include_past"),
    "book": ("receptionist", "slot_id option reason"),
    "cancel": ("receptionist", "appointment_id appointment_hint"),
    "reschedule": ("receptionist", "appointment_id appointment_hint slot_id option"),
    "policy": ("policy", "query"),
    "remember": ("memory", "category key value"),
    "forget": ("memory", "key"),
    "start_booking": ("coordinator", ""),
    "greet": ("coordinator", ""),
    "clarify": ("coordinator", ""),
    "refuse": ("coordinator", ""),
}
REQUIRED = {"policy": {"query"}, "remember": {"category", "key", "value"}, "forget": {"key"}}
WRITES = {"book", "cancel", "reschedule"}


@dataclass(frozen=True)
class Task:
    action: str
    fields: dict

    @property
    def owner(self):
        return SPECS[self.action][0]

    @property
    def label(self):
        return self.action.replace("_", " ").capitalize()

    @classmethod
    def parse(cls, raw):
        if not isinstance(raw, dict) or not isinstance(raw.get("action"), str) or raw["action"] not in SPECS:
            raise ValueError("Unknown task")
        action = raw["action"]
        fields = {key: value for key, value in raw.items() if key != "action"}
        if not fields.keys() <= set(SPECS[action][1].split()) or not REQUIRED.get(action, set()) <= fields.keys():
            raise ValueError("Unsupported or missing task fields")
        for key, value in fields.items():
            spec = FIELDS[key]
            if spec["type"] == "string" and (not isinstance(value, str) or not value.strip() or len(value) > 500):
                raise ValueError("Invalid text value")
            if spec["type"] == "boolean" and type(value) is not bool:
                raise ValueError("Invalid boolean")
            if spec["type"] == "array" and (not isinstance(value, list) or len(value) > 7 or
                                            any(not isinstance(item, str) or len(item) > 10 for item in value)):
                raise ValueError("Invalid weekday preferences")
            if spec["type"] == "integer" and (type(value) is not int or not 1 <= value <= 3):
                raise ValueError("Invalid option")
            if "enum" in spec and value not in spec["enum"]:
                raise ValueError("Invalid option")
            if key in {"date_from", "date_to"} and date.fromisoformat(value).isoformat() != value:
                raise ValueError("Use a calendar date in YYYY-MM-DD format")
        if "date_from" in fields and "date_to" in fields and fields["date_from"] > fields["date_to"]:
            raise ValueError("The end date must follow the start date")
        return cls(action, fields)


@dataclass(frozen=True)
class TurnPlan:
    goal: str
    tasks: tuple[Task, ...]

    @classmethod
    def parse(cls, raw):
        if not isinstance(raw, dict) or raw.keys() != {"goal", "tasks"}:
            raise ValueError("Invalid plan structure")
        goal, tasks = raw["goal"], raw["tasks"]
        if not isinstance(goal, str) or not goal.strip() or len(goal) > 140:
            raise ValueError("Invalid plan goal")
        if not isinstance(tasks, list) or not 1 <= len(tasks) <= 4:
            raise ValueError("Invalid plan length")
        validated = tuple(Task.parse(task) for task in tasks)
        if sum(task.action in WRITES for task in validated) > 1 or sum(task.action == "find_slots" for task in validated) > 1:
            raise ValueError("Use one booking change and one search per turn")
        if len(validated) > 1 and any(task.owner == "coordinator" for task in validated):
            raise ValueError("A scope decision must stand alone")
        return cls(goal, validated)


PLAN_TOOL = {
    "name": "submit_plan",
    "description": "Submit the single action plan for this user request.",
    "input_schema": {
        "type": "object", "required": ["goal", "tasks"],
        "properties": {
            "goal": {"type": "string", "description": "Short plain-language description of the intended action."},
            "tasks": {
                "type": "array", "minItems": 1, "maxItems": 4,
                "items": {"type": "object", "required": ["action"],
                          "properties": {"action": {"type": "string", "enum": list(SPECS)}, **FIELDS}},
            },
        },
    },
}
