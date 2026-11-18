# Prompt design

The Coordinator exposes one tool, `submit_plan`. The schema accepts a short goal and up to four tasks. The prompt describes supported actions and asks for typed fields such as an option number or a date.

| Context | Purpose |
|---|---|
| Clinic clock | Resolves phrases such as “tomorrow” against the simulated calendar. |
| Recent conversation and displayed slot choices | Resolves references such as “book the second one.” |
| Saved preferences | Gives scheduling context. Python applies saved defaults before explicit task fields. |
| Scope and tool contract | Describes the appointment and clinic-policy tasks the specialists can perform. |

Python validates the complete plan before dispatch. Emergency screening, account identity, human approval, permitted memory categories, and scheduling rules are enforced in code. The specialists handle execution and recovery, and the Coordinator formats replies from the verified results.
