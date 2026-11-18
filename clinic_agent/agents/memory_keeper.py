from __future__ import annotations

import re

from .base import Agent, AgentMessage

class MemoryKeeperAgent(Agent):
    role = "memory"

    def __init__(self):
        super().__init__("memory", "Memory Keeper", {
            "store": "SQLite", "whitelist": "non-clinical preferences only",
        })
        self.intentions = ["remember how this patient likes to be scheduled"]

    async def execute(self, task, context):
        fields = task.fields
        if task.action == "remember":
            result = self.save(context, fields)
        else:
            result = self.forget(context, fields["key"])
        return result

    def save(self, context, fields):
        result = context.ltm.remember(
            context.patient_id, fields["category"], fields["key"], fields["value"], session_id=context.session_id
        )
        if result["ok"]:
            saved = result["saved"]
            context.saved_facts[saved["key"]] = saved["value"]
        context.emit("memory", op="remember" if result["ok"] else "remember_rejected", detail=result)
        return result

    def forget(self, context, key):
        result = context.ltm.forget(context.patient_id, key)
        if key == "*":
            context.saved_facts.clear()
        else:
            norm = re.sub(r"[^a-z0-9_]+", "_", key.strip().lower()).strip("_")
            context.saved_facts.pop(norm, None)
        context.emit("memory", op="forget", detail={"key": key, **result})
        return result

    def summarize(self, context):
        rows = context.ltm.audit_rows(session_id=context.session_id)
        actions = [row["summary"] for row in rows if row["ok"] and row["human_confirmed"]]
        prefs = [f"Saved {key}={value}" for key, value in context.saved_facts.items()]
        summary = ". ".join(actions + prefs) or "General enquiry."
        context.ltm.add_session_summary(context.session_id, context.patient_id, summary)
        context.emit("memory", op="session_summary_saved", detail=summary)
        return summary

