# HIRI — CURRENT IMPLEMENTATION STATUS

## Verified checkpoint

Repository state verified directly on 2026-08-24.

### Direct WhatsApp Cloud readiness checkpoint

The direct WhatsApp provider path now includes these implemented milestones:

- Generic Integration Credential References — `f872135`
- Native WhatsApp Cloud outbound delivery — `63ec09d`
- Direct WhatsApp Cloud inbound provider edge — `0da83c4`

Automated verification covers the raw signed Meta webhook through normalized
Sales execution and the native WhatsApp Cloud delivery adapter without making a
real Meta network call. The coverage includes account and phone-number matching,
idempotent replay handling, Lead / Contact / external-identity binding, governed
WorkItems, approval and tool-access enforcement, provider delivery ID persistence,
delivery attempts, audit records, credential-value hygiene, and workspace isolation.

The legacy normalized WhatsApp endpoint remains available as a compatibility path.

### Direct Sales messaging channel status

| Channel | Automated status | Provider status |
| --- | --- | --- |
| WhatsApp Cloud | Direct signed inbound → governed Sales WorkItems → native outbound delivery is covered end to end. | Ready for real Meta test-business validation. |
| Facebook Messenger | Direct signed inbound → governed Sales WorkItems → native Graph API outbound delivery is covered end to end with a fake HTTP transport. | Ready for real Meta Page/app validation; no live Meta request has been made. |
| Instagram Direct | Direct signed inbound → governed Sales WorkItems → native Graph API outbound delivery is covered end to end with a fake HTTP transport. | Ready for real professional-account/app validation; no live Meta request has been made. |

The first Instagram Sales MVP explicitly uses **Instagram API with Facebook
Login** for professional accounts linked through Meta's Facebook Login model.
The configured `graph.facebook.com` host and Page access-token direction are
intentional for this mode. Supporting Instagram API with Instagram Login and
`graph.instagram.com` is a NEXT capability, not part of this implementation.

Messenger and Instagram use the existing IntegrationAccount, credential-reference,
Lead Capture, WorkItem, approval/tool-access, outbound-action, delivery-attempt, and
audit architecture. Their webhook app secret and API access token are resolved from
purpose-specific Integration Credential References. Existing legacy Meta accounts
may temporarily fall back to the account secret reference for webhook signature
verification.

Facebook/Instagram comment-to-DM remains an additional Sales Lead Capture trigger,
not the direct-messaging core. It reuses the normal Meta IntegrationAccount,
governed `send_message` WorkItem path, outbound action, credential references, and
native provider adapter; provider private-reply mapping is only a channel-specific
delivery detail. Both current Meta comment-private-reply contracts use the
configured provider account's `/messages` edge with `recipient.comment_id`;
Facebook uses the Page ID and Instagram uses the professional account ID.

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

Most recent verified full backend suite after direct Messenger and Instagram DM
readiness work:

- 862 passed
- 6 skipped
- 7 warnings
- 868 collected
- runtime: 199.59s

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
- native Facebook Messenger direct inbound/outbound Sales messaging
- native Instagram Direct inbound/outbound Sales messaging
- WhatsApp Cloud architecture
- WhatsApp Cloud integration
- WhatsApp normalization
- direct WhatsApp Cloud inbound provider edge

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
- generic integration credential references
- native WhatsApp Cloud outbound delivery adapter
- native Meta Graph delivery adapter for Messenger and Instagram Direct
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

Current work is real-provider validation and pilot readiness for direct WhatsApp,
Facebook Messenger, and Instagram Direct Sales messaging.

### Immediate priorities

1. Configure a dedicated Meta test-business application, WhatsApp test number, and callback URL in a secure non-production environment.
2. Run the signed webhook challenge and one real inbound text through the direct provider edge.
3. Confirm the governed reply reaches the sender and Meta delivery/status identifiers reconcile with HIRI audit data.
4. Exercise an approval-required reply and an operator-approved continuation with the pilot account.
5. Validate production callback TLS, secret injection/rotation, observability, rate limits, and operational runbooks.
6. Use real pilot-business testing to determine the next genuine Sales MVP gaps.

For Messenger, validate the Page access token, Page/app subscription permissions,
webhook callback, sender recipient mapping, Graph API version, and delivery/status
events with a Meta test user. For Instagram Direct, validate the supported
professional-account linkage, required messaging permissions, webhook subscription,
recipient mapping, Graph API version, and the platform's allowed messaging window
with a Meta test user. Rich media, templates, reactions, read receipts, delivery
status reconciliation, and provider-specific retry policy remain later work unless
the pilot proves one is required for the first useful Sales flow.

Redis, Celery, Temporal or another background runtime must not be introduced merely because they are available. Add worker infrastructure only when a demonstrated asynchronous or durable-execution requirement justifies it.

Do not reopen already-completed generic workforce foundations unless a concrete defect or missing MVP behavior is identified.

## Source-of-truth rule

For repository state, Git and verified tests win over older documents and conversation memory.

This file supersedes the previous `0accd3e` working-state checkpoint and the older `afb323e` / 610-passed checkpoint.
