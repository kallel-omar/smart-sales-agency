# HIRI — Documentation Cleanup Manifest

## Active source-of-truth set

Use only these as active project instructions:

1. `HIRI_MASTER_PLAN.md` — canonical architecture, product direction, MVP and NOW/NEXT/LATER.
2. `HIRI_CURRENT_IMPLEMENTATION_STATUS.md` — verified Git/test state only.
3. `HIRI_DEVELOPMENT_WORKFLOW.md` — coding/test/Codex workflow.
4. `HIRI_WEEKLY_EXECUTION_SYSTEM.md` — Google Sheet/task operating system.
5. `docs/design/HIRI_UI_UX_DESIGN_SYSTEM.md` — authoritative frontend/UI/UX rules.
6. ADRs — durable architecture decisions needing detailed rationale.

## Current decisions that must win over older notes

- HIRI is the only product/platform name.
- Sales is the first mature department; do not start new departments before the Sales MVP is proven.
- FastAPI owns the domain/backend.
- PostgreSQL/HIRI owns canonical business and workflow state.
- LangGraph owns AI orchestration/reasoning.
- Preserve HIRI's existing business-service, outbound-action and DeliveryAdapter/provider-adapter execution boundaries.
- Redis, Celery or another worker runtime is optional and should be introduced only when a demonstrated asynchronous or durable-execution requirement justifies it.
- Any future worker runtime is an executor only and must never own HIRI workflow or business state.
- n8n is optional and replaceable; production HIRI must operate without it.
- Temporal is LATER.
- Direct/native provider adapters are preferred for required production channels.
- The generic webhook adapter remains useful for arbitrary systems/optional bridges.
- HIRI must support external systems connecting into HIRI through authenticated workspace-scoped API/webhook boundaries.
- No visual workflow builder is required for the MVP.
- WorkItem remains the universal unit of work.
- Department → Supervisor → AIEmployee → Capability → Allowed Tool → Permissions/Policies → WorkItem → Approval → Business Result → Audit/Analytics remains the permanent architecture.
- The official HIRI UI/UX Design System is authoritative for frontend work.

## Files/notes to mark SUPERSEDED when found

Mark old copies as superseded if they conflict with the current decisions, especially documents that make any of the following active requirements:

- Taskiq + Valkey as the current execution runtime;
- n8n as a required production runtime;
- n8n as HIRI's workflow brain;
- Temporal as a NOW dependency;
- old separate product/department brand names;
- a roadmap that starts Marketing/Operations/Finance/HR before the Sales-first MVP;
- a generic AI/SaaS frontend style that conflicts with the HIRI UI/UX Design System.

Do not delete historical documents blindly. Archive them or prepend `SUPERSEDED — DO NOT USE FOR CURRENT IMPLEMENTATION` so history is preserved without confusing coding agents.

## Cleanup sequence

1. Promote the cleaned Master Plan to canonical name.
2. Mark/archive conflicting old plans.
3. Promote the cleaned Development Workflow.
4. Promote the cleaned Weekly Execution System.
5. Verify current Git state and full tests.
6. Update Current Implementation Status.
7. Audit repository gaps.
8. Rebuild the active task queue.
9. Update the Google Sheet.
10. Resume one-task-at-a-time coding.
