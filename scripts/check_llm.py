"""Verify that the configured live model can submit a CareBridge task plan."""
import _path  # noqa: F401
import asyncio

from clinic_agent.agents.planning import PLAN_TOOL, TurnPlan
from clinic_agent.config import get_settings
from clinic_agent.llm import build_llm
from clinic_agent.prompts import planning_prompt


async def main():
    settings = get_settings()
    try:
        planner = build_llm(settings)
        response = await asyncio.wait_for(
            planner.chat(system=planning_prompt(settings.clinic_name, settings.clinic_now),
                         messages=[{"role": "user", "content": "Find a General Medicine visit tomorrow morning."}],
                         tools=[PLAN_TOOL], max_tokens=settings.llm_max_tokens), timeout=30,
        )
        if len(response.tool_calls) != 1 or response.tool_calls[0].name != PLAN_TOOL["name"]:
            raise ValueError("The provider did not return a plan.")
        plan = TurnPlan.parse(response.tool_calls[0].input)
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        print(f"Planner check failed ({type(exc).__name__}{f', HTTP {status}' if status else ''}).")
        return 1
    print(f"Live planner accepted: {', '.join(task.action for task in plan.tasks)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
