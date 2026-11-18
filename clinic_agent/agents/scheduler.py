from __future__ import annotations

from .base import Agent, AgentMessage

class SchedulerAgent(Agent):
    role = "scheduler"

    def __init__(self):
        super().__init__("scheduler", "Slot Scheduler", {
            "protocol": "MCP", "tools": ["list_doctors", "find_available_slots", "get_slot"],
        })
        self.intentions = ["offer at most three slots that match preferences"]

    async def execute(self, task, context):
        options = dict(task.fields)
        if task.action in ("list_doctors", "doctor_hours") or options.get("doctor_name"):
            results = await context.mcp_call("list_doctors", {"department": options.get("department")})
            if not results.get("ok"):
                return results
            name = options.pop("doctor_name", "").casefold().removeprefix("dr. ").strip()
            doctors = [doctor for doctor in results["doctors"]
                       if not name or name in doctor["name"].casefold()]
            if task.action != "find_slots":
                return {"ok": True, "doctors": doctors}
            if len(doctors) != 1:
                return {"ok": False, "message": "Choose a doctor from the directory to narrow the search."}
            options["doctor_id"] = doctors[0]["doctor_id"]

        options = {**context.preferences(), **options}
        options.update(limit=3)
        result = await context.mcp_call("find_available_slots", options)
        if result.get("ok"):
            context.slot_filters = options
        return result

    def propose(self, service, request):
        result = service.find_available_slots(department=request.content["department"], limit=1)
        slots = result.get("slots", [])
        self.messages_handled += 1
        self.last_act = "PROPOSE" if slots else "REFUSE"
        return AgentMessage(self.agent_id, request.sender, self.last_act,
                            slots[0] if slots else {"reason": "NO_SLOT"}, in_reply_to=request.id)
