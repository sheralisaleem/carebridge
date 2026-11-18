# Requirements and test coverage

| Requirement | Implementation | Evidence |
|---|---|---|
| One interpretation call and real delegation | `agents/planning.py`, `agents/coordinator.py` | `test_agent.py`, Assistant activity |
| Doctor-owned hours and agenda | `doctor_schedule.py`, `web.py` | `test_doctor_schedule.py`, `test_web_accounts.py` |
| Short- and long-term memory | `memory.py`, `agents/memory_keeper.py` | Ordinal and fresh-session tests |
| Policy retrieval | `rag.py`, `agents/policy.py` | `test_rag.py`, Knowledge scores and sources |
| Real MCP client and server | `mcp_client.py`, `mcp_server.py`, `clinic_tools.py` | `test_mcp_integration.py` |
| Approval and isolation | `agents/front_desk.py`, `web_session.py`, `auth.py` | Decline, expiry, reset and account tests |
| Replanning and shared simulation | `agents/coordinator.py`, `simulation/runner.py` | Slot race, doctor leave, and ABM tests |
| Responsive pages and agent activity | `web/templates/`, `web/static/` | Desktop and narrow browser walkthrough |
