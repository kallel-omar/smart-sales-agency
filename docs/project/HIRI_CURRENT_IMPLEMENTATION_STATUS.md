# HIRI — CURRENT IMPLEMENTATION STATUS

## Verified checkpoint

Repository state verified directly on 2026-08-21.

### Stable product checkpoint

- Product checkpoint commit: `fb981d4`
- Commit: `feat: complete HIRI spatial landing hero`
- `ui/hiri-full-redesign` points to this checkpoint
- `feat/integration-credential-references` was created from this checkpoint but feature coding has not started

### Documentation-cleanup checkpoint

- Current branch: `docs/hiri-project-cleanup`
- Verified documentation-cleanup checkpoint: `febef23`
- At cleanup checkpoint: 45 commits ahead of `origin/main`, 0 behind
- Working tree was **clean** immediately after documentation cleanup commit `febef23`
- Documentation cleanup is committed; no product/backend/frontend feature code was included in that cleanup commit

No product/backend/frontend feature code has been added on the documentation-cleanup branch.

## Frontend redesign checkpoint

The public HIRI spatial landing hero is committed at `fb981d4`.

Verified frontend checks:
- 56 tests passed across 7 test files
- production Vite build passed
- staged diff check passed before commit
- unused 1.9 MB hero image was removed before commit
- old `Selino` reference was removed from the hero
- fake public-facing live metrics were replaced with factual architectural wording
- working tree was clean at the `fb981d4` product checkpoint

The public-site redesign task is closed.

## Backend test baseline

Most recent verified full backend suite before the final frontend-only commit:

- 803 passed
- 6 skipped
- 7 warnings
- 809 collected
- runtime: 207.02s

The final commit `fb981d4` is frontend-only.

Current warnings remain non-blocking:
- Starlette/FastAPI TestClient deprecation
- SQLModel `session.query()` deprecation warnings

Do not perform unrelated dependency cleanup solely to remove these warnings.

## Verified implemented HIRI areas

The current test suite verifies substantial implementation beyond the old checkpoint, including:

### Generic workforce foundation
- workspace/tenant foundation
- departments
- department supervisors
- AI employees
- business capabilities
- AIEmployee ↔ Capability assignments
- AI employee tool access governance
- operator assignment

### Work execution and governance
- WorkItems
- WorkItem approvals
- approval decision attribution
- Sales WorkItem execution
- follow-up WorkItems
- outbound capability validation
- workspace isolation / RBAC

### AI Gateway / AI economics
- AI invocation boundary
- AI invocation gateway
- AI usage accounting
- AI model routing
- AI model tiers
- AI execution attribution
- workspace AI usage limits
- AI cost accounting

### Shared business data
- leads
- customer contacts
- conversations
- workspace Sales instructions

### Sales MVP
- Sales department service
- Sales conversation quality
- commercial grounding
- objection / closing policies
- handoff policies and lifecycle
- stage transition service
- WorkItem-driven Sales execution
- WorkItem-driven follow-up
- Sales MVP end-to-end test

### Lead capture and channels
- generic lead capture
- lead capture channels
- inbound integrations
- Meta inbound lead capture
- social comment automation
- WhatsApp Cloud architecture
- WhatsApp Cloud integration
- WhatsApp normalization

### Integrations / delivery / audit
- integration lifecycle
- secret reference policy
- correlation and execution tracing
- outbound delivery
- retries
- cancellation / expiration
- delivery readiness
- provider status events
- generic webhook delivery adapter
- integration health / operational summaries

### UI / analytics
- operator UI API
- operator analytics
- business/workforce analytics
- authenticated HIRI app experience
- HIRI UI/UX design system
- public HIRI marketing experience
- spatial public landing hero

## Important architecture implication

The earlier roadmap that placed Department, Capability, AIEmployee, WorkItem, approval linkage, Lead Capture, Meta social capture, analytics, and Sales end-to-end execution as future work is obsolete.

Those areas must be treated as existing implementation to preserve and extend.

## Current next phase

The frontend redesign checkpoint is complete.

Current work is the remaining Sales MVP gap audit and dependency-ordered NOW planning before new feature coding resumes.

### Immediate priorities

1. Audit the remaining Sales MVP gaps against the cleaned HIRI Master Plan.
2. Build the dependency-ordered NOW queue from verified remaining gaps.
3. Classify every remaining gap as Implemented / Partial / Missing / NEXT / LATER without reopening completed workforce foundations.
4. Preserve existing business-service, outbound-action and DeliveryAdapter/provider-adapter architecture.
5. Add generic integration credential references where direct provider adapters require multiple external credentials.
6. Complete the direct WhatsApp Cloud outbound adapter path.
7. Complete the direct WhatsApp Cloud inbound provider boundary so production does not require n8n.
8. Verify the complete real-channel Sales flow: Capture → Understand → Qualify → Converse → Follow up → Convert.
9. Validate workspace isolation, permissions, approvals, AI usage/cost controls and audit behavior end to end.
10. Use real pilot-business testing to determine the next genuine MVP gaps.

Redis, Celery, Temporal or another background runtime must not be introduced merely because they are available. Add worker infrastructure only when a demonstrated asynchronous or durable-execution requirement justifies it.

Do not reopen already-completed generic workforce foundations unless a concrete defect or missing MVP behavior is identified.

## Source-of-truth rule

For repository state, Git and verified tests win over older documents and conversation memory.

This file supersedes the previous `0accd3e` working-state checkpoint and the older `afb323e` / 610-passed checkpoint.
