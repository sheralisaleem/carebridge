from __future__ import annotations

from .base import Agent, AgentMessage

class ReceptionistAgent(Agent):
    role = "receptionist"

    def __init__(self):
        super().__init__("receptionist", "Front Desk", {
            "protocol": "MCP",
            "tools": ["book_appointment", "cancel_appointment", "reschedule_appointment",
                      "list_my_appointments"],
            "requires_human_confirm": True,
        })
        self.intentions = ["never change the diary without human confirmation"]

    async def execute(self, task, context):
        if task.action == "list_appointments":
            return await self.diary(context, include_past=task.fields.get("include_past", False))

        action = task.action
        fields = task.fields
        appointment = None
        slot = None
        args = {"patient_id": context.patient_id}
        if action != "book":
            diary = await self.diary(context)
            if not diary.get("ok"):
                return diary
            choices = [a for a in diary.get("appointments", []) if a["appointment_id"] == fields.get("appointment_id")
                       or not fields.get("appointment_id")]
            if not fields.get("appointment_id") and (hint := fields.get("appointment_hint")):
                choices = [a for a in choices if any(hint.casefold() in str(a.get(key, "")).casefold()
                                                    for key in ("date", "doctor_name", "department", "reason"))]
            choices = [a for a in choices if a["status"] == "confirmed" or
                       (action == "reschedule" and a["status"] == "cancelled_by_clinic")]
            if len(choices) != 1:
                return {"ok": False, "message": "Select the appointment you want to change.", "appointments": choices,
                        "appointment_action": action}
            appointment = choices[0]
            args["appointment_id"] = appointment["appointment_id"]
        if action != "cancel":
            reference = fields.get("slot_id")
            if action == "reschedule" and not (reference or fields.get("option")):
                return {"ok": True, "appointments": [appointment], "message": "Let’s look for a new time."}
            if fields.get("option"):
                index = fields["option"] - 1
                reference = context.slots[index]["slot_id"] if 0 <= index < len(context.slots) else None
            if not reference or not context.accepts_slot(reference):
                return {"ok": False, "message": "Choose a time from the available options before confirming."}
            result = await context.mcp_call("get_slot", {"slot_id": reference})
            if not result.get("ok"):
                return result
            slot = result["slot"]
            if slot["status"] != "open":
                return {"ok": False, "error_code": "SLOT_UNAVAILABLE",
                        "message": "That time is no longer available.", "slot": slot,
                        "reschedule_id": args.get("appointment_id")}
            args["new_slot_id" if action == "reschedule" else "slot_id"] = reference

        summary = action.upper() + " "
        if appointment:
            summary += self.describe(appointment) + f" [{appointment['appointment_id']}]"
        if slot:
            summary += (" -> " if appointment else "") + self.describe(slot)
        if action == "book" and fields.get("reason"):
            args["reason"] = fields["reason"][:200]
            summary += f". Reason: {args['reason']}"
        context.waiting(self.agent_id)
        approved = bool(context.confirm and await context.confirm(summary))
        context.emit("confirm", summary=summary, approved=approved)
        tool = f"{action}_appointment"
        if not approved:
            context.ltm.log_audit(session_id=context.session_id, patient_id=context.patient_id, tool=tool, args=args,
                                  human_confirmed=False, ok=False, summary=f"Declined {summary}")
            return {"ok": False, "error_code": "USER_DECLINED", "message": "The appointment change was declined."}

        context.applying(self.agent_id)
        result = await context.mcp_call(tool, args)
        if slot:
            result.setdefault("slot", slot)
        if action == "reschedule":
            result.setdefault("reschedule_id", args["appointment_id"])
        ok = result.get("ok") is True
        audit = result.get("message") or summary
        if ok and result.get("appointment"):
            visit = result["appointment"]
            audit = f"{audit} {self.describe(visit)} [{visit['appointment_id']}]"
        context.ltm.log_audit(session_id=context.session_id, patient_id=context.patient_id, tool=tool, args=args,
                              human_confirmed=True, ok=ok, summary=audit)
        if result.get("error_code") in {"TIMEOUT", "MCP_ERROR", "INVALID_RESULT", "INTERNAL_ERROR"}:
            refresh = await self.diary(context)
            return {"ok": False, "message": "The result could not be verified. Check your current appointments before trying again.",
                    "appointments": refresh.get("appointments", [])}
        return result

    async def diary(self, context, include_past=False):
        return await context.mcp_call("list_my_appointments",
                                      {"patient_id": context.patient_id, "include_past": include_past})

    @staticmethod
    def describe(item):
        return f"{item['doctor_name']} ({item['department']}), {item['date']} at {item['start_time']}"

    def book_synthetic(self, service, request):
        result = service.book_appointment(request.sender, request.content["slot_id"])
        self.messages_handled += 1
        self.last_act = "CONFIRM" if result["ok"] else "REFUSE"
        content = {"appointment_id": result["appointment"]["appointment_id"]} if result["ok"] else {
            "reason": result["error_code"]
        }
        return AgentMessage(self.agent_id, request.sender, self.last_act, content, in_reply_to=request.id)
