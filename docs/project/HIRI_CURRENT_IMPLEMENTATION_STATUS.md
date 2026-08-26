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

### Real WhatsApp Sales E2E — VERIFIED

On 2026-08-24, HIRI completed a real WhatsApp Sales end-to-end validation. A
real inbound WhatsApp message reached HIRI through the Meta Cloud API, HIRI
identified the correct workspace and IntegrationAccount, and Sales routing
assigned the work through the existing AIEmployee / Capability / WorkItem
architecture. The `answer_customer` execution completed, and governed
`send_message` execution used the native WhatsApp Cloud adapter. Meta's
`POST /messages` returned HTTP 200, and the real reply was received on the
phone. Real WhatsApp Sales E2E is therefore **VERIFIED**.

Task 288 reliability hardening was verified with the following behavior:

- HTTP 401 remains classified as an authentication failure.
- HTTP 403 is classified separately as provider permission denied.
- HTTP transport failures remain temporary network errors.
- Failed inbound processing releases its event reservation for provider retry.
- Successfully processed duplicate events remain idempotent.
- Full backend suite: 865 passed, 6 skipped, 7 existing warnings.

### Task 289 — Real Facebook Messenger Sales E2E — VERIFIED / DONE

On 2026-08-24, HIRI completed a real Facebook Messenger Sales end-to-end
validation for provider `facebook_messenger`, Facebook Page ID
`1302062409649643`, and HIRI IntegrationAccount ID
`06c36437-c2c2-4bb4-be14-36a9a174d3ae`.

- The Messenger Page Access Token is configured through
  IntegrationCredentialReference purpose `api_access_token`; its secret value
  is not stored in the database.
- `webhook_app_secret` uses the existing Meta App Secret reference.
- `webhook_verify_token` is configured separately for Messenger.
- Meta webhook callback verification succeeded.
- The Page webhook field `messages` was subscribed successfully.
- A real Messenger inbound message reached HIRI and HIRI Sales routing executed.
- HIRI sent a real automated reply through Facebook Messenger.

Real Facebook Messenger Sales E2E is therefore **VERIFIED / DONE**.

The real reply greeted the prospect as “Unknown”. Messenger identity and
display-name enrichment is a **NOW — small hardening task**. This does not
invalidate the successful Messenger transport or end-to-end validation.

### Direct Sales messaging channel status

| Channel | Automated status | Provider status |
| --- | --- | --- |
| WhatsApp Cloud | Direct signed inbound → governed Sales WorkItems → native outbound delivery is covered end to end. | Real WhatsApp Sales E2E verified on 2026-08-24. |
| Facebook Messenger | Direct signed inbound → governed Sales WorkItems → native Graph API outbound delivery is covered end to end. | Real Facebook Messenger Sales E2E verified on 2026-08-24; identity/display-name enrichment is NOW hardening. |
| Instagram Direct | Direct signed inbound → governed Sales WorkItems → native Graph API outbound delivery supports both Facebook Login and native Instagram Login routing. | Native Instagram Login real Sales E2E verified on 2026-08-25; Facebook Login inbound remains verified while outbound is externally blocked by Meta application/capability access in the current test environment. |
| TikTok Business Messaging | Mocked signed direct-DM inbound → governed Sales WorkItems → native outbound delivery, plus provider-gated Comment-to-Message Social Lead Capture, is implemented. | Real TikTok provider E2E remains blocked by onboarding and Business Messaging API access. |

### Task 293 — TikTok Business Messaging foundation

HIRI now includes a mocked-provider-tested `tiktok_dm` foundation for direct DM
inbound/outbound Sales messaging and TikTok Comment-to-Message as an additional
Social Lead Capture trigger. Both paths reuse the existing IntegrationAccount,
Sales, AIEmployee, Capability, WorkItem, tool-access, approval, outbound-action,
delivery-attempt, and audit architecture.

Comment-to-Message eligibility is fail-closed and disabled until explicitly
confirmed for the connected TikTok Business Account. Real TikTok provider E2E
remains **BLOCKED** by external onboarding and API access. Automatic TikTok token
refresh remains future credential-lifecycle work; current delivery safely exposes
a reconnect-required failure state without persisting token values.

Latest verified backend result: **895 passed, 6 skipped, 25 warnings**. This is an
automated mocked-provider checkpoint and does not claim live TikTok validation.

### Task 294A — Sales workforce foundation hardening

HIRI's canonical default Sales workforce now converges idempotently to four
specialist roles with six capability assignments:

- `lead_research`: `capture_lead`, `research_company`
- `qualification`: `qualify_lead`
- `sales_conversation`: `answer_customer`, `send_message`
- `follow_up`: `follow_up_lead`

Default provisioning reuses clearly recognized canonical employees and does not
repurpose custom same-role employees. WorkItem parent references are validated
within the workspace, child WorkItems inherit the parent correlation, and known
`send_message` targets are routed only to assignments with matching tool access.
Rejecting an approval linked to an `approval_required` WorkItem now leaves that
WorkItem in the terminal `cancelled` state without sending an external action.

Latest verified backend result: **911 passed, 6 skipped, 25 warnings**.

### Task 294B — Persisted Sales acquisition coordination

The Sales acquisition chain `capture_lead → research_company → qualify_lead`
now uses persisted WorkItems as its authoritative business execution path. Lead
Research owns capture and research, while Qualification owns qualification. The
three stages preserve one correlation trace and explicit parent-child lineage.

The result coordinator is deterministic and non-LLM: it creates only the next
required WorkItem from a completed structured result. The legacy workflow API
remains compatible, but no longer performs a second in-memory research or
qualification execution. Qualification completion is terminal for acquisition
and does not automatically initiate outreach or block the independent live
conversation path.

Simultaneous first-time acquisition-root creation remains **NEXT** concurrency
hardening; normal retries reuse the persisted root and downstream children.

Latest verified backend result: **917 passed, 6 skipped, 25 warnings**.

### Task 295B — Channel connection lifecycle foundation

HIRI now models provider-neutral channel connection lifecycle states as
`configured`, `connected`, `reconnect_required`, and `disconnected`. The
`active` flag remains an independent execution switch: newly configured customer
channels remain inactive until validated and enabled, while migrated active
accounts retain their established runtime behavior.

Provider/auth-mode requirements now define allowed credential purposes and drive
safe readiness and credential-expiry metadata. Confirmed permanent authentication
failures are non-retryable and move only the affected account to
`reconnect_required`; transient network, rate-limit, and provider failures retain
their existing retry behavior. Disable preserves connection configuration and
credentials, while disconnect disables the account, clears its stored credential
references, and preserves its business, action, and audit history for safe reuse.

Active provider-identity ownership is protected for customer messaging channels,
and lifecycle operations do not grant AIEmployee tool access automatically. This
is an operator-assisted foundation only: provider-side validation, OAuth, token
refresh/rotation, writable SecretStore support, and customer self-service channel
connection remain incomplete.

Latest verified backend result: **930 passed, 7 skipped, 39 warnings**.

### Task 295C — Provider connection validators — COMPLETE for current MVP scope

The operator-assisted channel connection foundation and current MVP validators
are complete at these checkpoints:

- Task 295B — Channel Connection Lifecycle Foundation: `007c6ac feat: add channel connection lifecycle foundation`
- Task 295C1 — WhatsApp Cloud validator: `dfe256b feat: validate whatsapp channel connections`
- Task 295C2 — Instagram Native Login validator: `682ca46 feat: validate native instagram channel connections`
- Task 295C3 — Facebook Messenger validator: `fca76c6 feat: validate messenger channel connections`

The validated MVP provider scope is WhatsApp Cloud, Instagram Native Login, and
Facebook Messenger. Validation is backend/operator-assisted, does not activate a
channel automatically, and does not grant AIEmployee tool access automatically.
OAuth and self-service onboarding are not implemented. TikTok live validation
is not implemented, and no claim is made that TikTok is live. An Instagram
Facebook Login validator is outside the current MVP validator scope.

Provider-side webhook/subscription readiness is verified only where official
read-only provider contracts and HIRI's current persisted context allow it.
Some subscription, application-review, and permission state remains not
independently verifiable. HIRI therefore does not claim that all provider
onboarding is self-service. Validator expansion beyond these three providers is
**PARKED / NEXT**, not NOW.

Task 290 validated a real Instagram professional-account inbound DM through the
Meta webhook and existing HIRI Sales architecture. HIRI created the governed
`SEND_MESSAGE` outbound action, but Meta rejected the Facebook Login Graph API
request with OAuthException code 3 because the application lacked capability for
that call. This established the need for native Instagram Login support without
invalidating the verified inbound, routing, WorkItem, or governance path.

Task 291 extends the same `instagram_dm` provider with two explicit connection
modes. Existing and mode-omitting accounts use `facebook_login` with
`graph.facebook.com`; controlled pilot accounts may explicitly use
`instagram_login` with `graph.instagram.com`. The authentication mode is
non-secret IntegrationAccount routing configuration. API tokens remain external
secrets referenced through purpose `api_access_token`, and each account's
`webhook_app_secret` must reference the secret for the application that actually
signs its webhook. Customer-facing OAuth, token exchange/refresh, and writable
secret-manager onboarding remain NEXT.

### Native Instagram Login real Sales E2E — VERIFIED / DONE

On 2026-08-25, HIRI validated native Instagram Login end to end with a real
Instagram Professional account. The verified IntegrationAccount uses provider
`instagram_dm`, account-level `provider_auth_mode=instagram_login`, external
account ID `17841439019937286`, and IntegrationAccount ID
`f2ecd496-b901-4e80-9094-da480cf646dd`.

The verified real flow was:

`Instagram direct message → Meta native Instagram webhook → HIRI inbound endpoint
→ workspace-scoped inbound receipt → Sales processing → Sales Conversation
AIEmployee → send_message capability → controlled_automation tool access → native
Instagram outbound delivery through graph.instagram.com → real Instagram reply
received by the sender`.

The following components were validated against real Meta infrastructure:

- Instagram Professional account authorization and a native Instagram User access token
- explicit `instagram_login` authentication mode and `graph.instagram.com` routing
- native webhook callback verification and `messages` webhook subscription
- signed inbound webhook processing and durable inbound receipt creation
- Sales routing and Sales Conversation AIEmployee execution
- `send_message` capability assignment
- IntegrationAccount-specific tool-access governance with `controlled_automation` autonomy
- credential references for `api_access_token`, `webhook_app_secret`, and `webhook_verify_token`
- outbound Meta delivery and a real reply visible in Instagram

Native Instagram Login real Sales E2E is therefore **VERIFIED / DONE**.

The Facebook Login mode remains distinct: provider `instagram_dm` with
`provider_auth_mode=facebook_login` continues to route through
`graph.facebook.com`. Its inbound webhook flow was previously verified. Outbound
sending remains externally blocked by Meta application/capability access in the
current test environment; this is not a HIRI architectural failure. Native
Instagram Login now provides the successfully verified Instagram-only path.

Two **NOW — small hardening tasks** were exposed without invalidating the E2E
result:

1. Enrich Instagram sender/profile identity where Meta permits it and use a safe
   fallback when no display name can be resolved, instead of greeting the sender
   as “Unknown”.
2. Ensure generated customer-facing approval/autonomy wording reflects the actual
   execution state. An action executed under `controlled_automation` with
   `requires_approval=False` must not claim that human approval occurs before send.

The real validation used a temporary Cloudflare quick tunnel. Quick tunnels are
development/testing infrastructure only and must not become a production
dependency. No access tokens, app secrets, webhook verify tokens, credentials,
passwords, or other secret values are recorded here.

This validation confirms the existing architecture without a separate Instagram
system: `Workspace → Sales Department → AIEmployee → Capability →
IntegrationAccount → permission/tool access → WorkItem / execution → business
result → audit`. Native Instagram Login remains the generic `instagram_dm`
provider with account-level `provider_auth_mode`; no duplicate Sales, Lead,
WorkItem, approval, or audit system was introduced.

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

Most recent verified full backend suite after Task 288 WhatsApp live reliability
hardening:

- 865 passed
- 6 skipped
- 7 existing warnings
- 871 collected
- runtime: 262.27s

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

Task 295C is complete for the current MVP provider-validator scope. The next
major development phase is **Task 296 — Sales AI Expertise / Skills**. Task 296A
is an architecture and implementation audit before any Task 296 implementation;
implementation details are not yet defined.

Provider-validator expansion beyond WhatsApp Cloud, Instagram Native Login, and
Facebook Messenger remains **PARKED / NEXT**. Existing production and pilot
readiness limitations continue to apply, including Meta permission and
application-capability constraints where relevant.

### Immediate priorities

1. Harden Messenger and Instagram sender/profile enrichment with safe display-name fallbacks.
2. Align generated customer-facing approval/autonomy wording with actual execution state.
3. Exercise an approval-required reply and an operator-approved continuation with the pilot account.
4. Validate production callback TLS, secret injection/rotation, observability, rate limits, and operational runbooks.
5. Reconcile Meta delivery/status identifiers with HIRI audit data across the verified channels.
6. Use real pilot-business testing to determine the next genuine Sales MVP gaps.

For the verified Meta channels, continue validating production permissions,
recipient mapping, Graph API versions, allowed messaging windows, and
delivery/status events as pilot configuration changes. Rich media, templates,
reactions, read receipts, delivery status reconciliation, and provider-specific
retry policy remain later work unless the pilot proves one is required for the
first useful Sales flow.

Redis, Celery, Temporal or another background runtime must not be introduced merely because they are available. Add worker infrastructure only when a demonstrated asynchronous or durable-execution requirement justifies it.

Do not reopen already-completed generic workforce foundations unless a concrete defect or missing MVP behavior is identified.

## Source-of-truth rule

For repository state, Git and verified tests win over older documents and conversation memory.

This file supersedes the previous `0accd3e` working-state checkpoint and the older `afb323e` / 610-passed checkpoint.
