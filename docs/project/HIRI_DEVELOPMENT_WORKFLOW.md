# HIRI — DEVELOPMENT WORKFLOW

## Purpose

This document defines **how HIRI should be developed efficiently, safely, and with minimum wasted AI quota/cost**.

It complements:

- `HIRI_MASTER_PLAN.md` → what HIRI is and where it is going
- `HIRI_CURRENT_IMPLEMENTATION_STATUS.md` → the current real code state
- this file → how development work should be executed

This workflow is a project rule.

The objective is not to generate the most code.

The objective is to **finish HIRI correctly, quickly, with controlled cost, clean architecture, and reliable tests**.

---

# 1. Core Development Principle

For every task:

Understand
→ inspect existing code
→ reuse what already exists
→ research only when needed
→ choose the simplest architecture-compatible solution
→ choose the right coding tool
→ implement a small change
→ test
→ review
→ commit
→ continue

Do not jump directly from an idea to coding.

---

# 2. Preserve the Existing HIRI Architecture

All implementation must remain compatible with the HIRI master architecture:

Workspace / Tenant
→ Department
→ Department Supervisor
→ AIEmployee
→ Capability
→ Allowed Tool / Integration / MCP
→ Permissions / Policies
→ WorkItem
→ Approval when required
→ Business Result
→ Audit / Analytics

Also preserve:

- FastAPI as backend/domain owner
- LangGraph as AI orchestration layer where AI orchestration is required
- PostgreSQL/HIRI as canonical owner of business and workflow state
- existing business-service, outbound-action and DeliveryAdapter/provider-adapter execution boundaries
- direct provider adapters for required production integrations
- generic webhook compatibility where useful
- n8n as an optional replaceable integration bridge only; HIRI must operate without it
- AI Gateway as model/provider control layer
- strict tenant/workspace isolation
- shared HIRI business data
- reusable capabilities
- auditable actions

Redis, Celery or another worker runtime is not a required HIRI architectural layer. Introduce background-worker infrastructure only when a demonstrated asynchronous or durable-execution requirement justifies it.

If worker infrastructure is introduced, it must remain replaceable, reload canonical HIRI state where appropriate, respect workspace permissions and approvals, and never become the owner of WorkItem or business state.

Do not create a new parallel architecture for a single feature.

# 3. Before Coding Any Task

Before implementation, answer these questions:

1. What exact problem does this task solve?
2. Is it required for NOW, NEXT, or LATER?
3. Which HIRI layer owns it?
4. Which Department owns it?
5. Which AIEmployee needs it?
6. Is it a capability, tool, workflow, shared service, or domain object?
7. Does similar code already exist?
8. Can an existing abstraction be extended instead of creating another one?
9. Does it affect workspace isolation?
10. Does it affect permissions or approvals?
11. Does it affect audit history?
12. Does it affect AI cost?
13. What focused tests prove the change?
14. What could break?

If the task cannot be placed clearly in the existing architecture, stop and resolve the architectural question before coding.

---

# 4. Inspect the Repository First

Never ask an AI coding tool to build a feature before checking the repository.

Before changing code:

- inspect relevant models
- inspect services
- inspect API routes
- inspect existing capabilities
- inspect permissions
- inspect tests
- inspect naming conventions
- inspect similar previous implementations
- inspect migrations/configuration if relevant

Prefer:

**extend existing code**

over:

**create a second implementation of the same concept**

Duplicate abstractions are a long-term cost.

---

# 5. Research-Before-Coding Rule

Research is useful when the task depends on:

- a current framework/API
- security best practices
- a new integration
- Meta/WhatsApp/Instagram APIs
- MCP changes
- FastAPI/LangGraph changes
- authentication standards
- payment/security requirements
- current provider limitations
- unfamiliar architecture patterns
- a technical choice with multiple serious alternatives

For these tasks:

1. inspect official documentation first
2. inspect trusted primary sources
3. inspect proven open-source implementations when useful
4. compare with existing HIRI code
5. choose the simplest compatible approach

Do not research for hours when the repository already contains a correct pattern.

Research should reduce uncertainty, not delay development.

---

# 6. Open-Source Reuse Rule

When finding an open-source project, classify it as:

## A — Reuse Code

Use a specific component if:

- license allows it
- code quality is acceptable
- it solves the exact problem
- it fits HIRI architecture
- adapting it is faster than rebuilding it
- it does not introduce unnecessary dependencies

## B — Reuse Pattern

Study the architecture/approach but implement it in HIRI's own structure.

## C — Integration

Use the project/service as an external tool.

## D — Ignore

Ignore it if:

- it duplicates HIRI
- it adds unnecessary complexity
- it forces architecture changes without strong benefit
- maintenance risk is too high
- it solves a problem HIRI does not currently have

Never rebuild HIRI around a repository just because the repository looks impressive.

---

# 7. AI Tool Strategy

Use the right tool for the right task.

The main development tools can include:

- ChatGPT
- Codex
- normal IDE/editor
- Git
- tests
- official documentation
- trusted open-source repositories

No single tool should be used for everything.

---

# 8. ChatGPT Role

Use ChatGPT primarily for:

- architecture decisions
- roadmap planning
- deciding the next task
- reviewing task boundaries
- research
- comparing implementation options
- debugging strategy
- interpreting test failures
- code review reasoning
- preparing precise Codex prompts
- detecting duplicated concepts
- checking HIRI master-plan consistency
- deciding whether a feature is NOW/NEXT/LATER

ChatGPT should often decide **what to do before Codex writes code**.

This reduces wasted coding attempts and wasted quota.

---

# 9. Codex Quota Strategy

Codex quota is valuable.

Do not spend it on trivial tasks that can be handled with small manual edits or straightforward implementation.

Reserve Codex primarily for high-value work such as:

- repository-wide reasoning
- architecture-sensitive changes
- complex refactors
- difficult bugs
- data migrations
- tenant-isolation changes
- permissions/security changes
- approval architecture
- WorkItem orchestration changes
- AI Gateway changes
- multi-file feature implementation
- unfamiliar code paths
- complex tests
- integration work involving several layers
- cases where understanding many files at once gives a real advantage

Avoid wasting Codex quota on:

- renaming one variable
- changing copy/text
- simple CRUD additions following an existing pattern
- repetitive boilerplate
- obvious one-file changes
- formatting
- simple tests copied from an established pattern
- tasks that can be handled reliably without Codex

Codex should be used where its reasoning saves more time than it costs.

---

# 10. Prepare Before Calling Codex

Do not give Codex vague instructions such as:

"Build the Sales department."

Instead prepare a precise task.

A good Codex task should include:

- task number/name
- business goal
- exact architecture constraints
- relevant existing files/concepts
- what must remain unchanged
- expected behavior
- workspace isolation requirements
- permission/approval rules
- required tests
- commands to run
- stop conditions
- commit rules

This reduces repeated attempts and quota usage.

---

# 11. Codex Prompt Template

Use a structure similar to:

## Task

Implement: `<specific task>`

## Goal

`<exact business/technical outcome>`

## Preserve

- existing HIRI architecture
- existing completed tasks
- tenant isolation
- FastAPI domain ownership
- LangGraph orchestration
- current API contracts unless explicitly changed

## Reuse

Inspect existing implementations before creating new abstractions.

## Requirements

- requirement 1
- requirement 2
- requirement 3

## Tests

Run focused tests first.

Then run:

`python -m pytest`

Use the repository-configured Python executable if required.

## Stop Conditions

Stop immediately if:

- tests fail unexpectedly
- architecture conflicts with existing implementation
- required dependency is missing and installation was not authorized
- task requires redesign outside its scope

## Git

- modify only files required for this task
- no unrelated cleanup
- one commit for the task when instructed
- do not push unless explicitly requested

---

# 12. Tool Escalation Rule

Use the simplest capable tool.

Suggested order:

### Level 1 — Manual / IDE

Use when the change is tiny and obvious.

### Level 2 — ChatGPT Planning + Local Implementation

Use when reasoning is needed but coding itself is straightforward.

### Level 3 — Codex

Use for complex repository work.

### Level 4 — Research + ChatGPT + Codex

Use when both the architecture/technology and implementation are complex.

Do not escalate automatically.

---

# 13. One Task at a Time

HIRI development should follow small sequential tasks.

Each task should have:

- one clear purpose
- limited scope
- clear acceptance criteria
- focused tests
- full-suite verification
- one logical commit when committing is part of the workflow

Avoid giant tasks that mix:

- domain changes
- UI changes
- integrations
- refactors
- infrastructure
- unrelated cleanup

Small tasks are easier to verify, debug, revert, and transfer between coding environments.

---

# 14. Test Discipline

Testing is mandatory.

For each coding task:

## Step 1 — Focused Tests

Run the smallest relevant tests first.

Examples:

`python -m pytest tests/test_specific_feature.py`

or a specific test case.

## Step 2 — Full Suite

After focused tests pass:

`python -m pytest`

Use the repository-configured Python executable if needed.

## Step 3 — Stop on Failure

If any unexpected test fails:

- stop
- inspect the failure
- do not continue to the next task
- do not hide the failure
- do not modify unrelated code just to make tests green

Resolve the actual cause.

---

# 15. Regression Protection

A new feature must not silently break previous completed tasks.

Before changing a shared abstraction, identify:

- which endpoints use it
- which tests cover it
- which departments depend on it
- whether tenant isolation can be affected
- whether audit behavior changes
- whether permissions change
- whether API compatibility changes

Prefer backward-compatible changes unless the roadmap explicitly requires a breaking change.

---

# 16. Tenant Isolation Is a Stop-Level Requirement

Workspace isolation is one of HIRI's most important guarantees.

Any feature involving:

- queries
- leads
- customers
- conversations
- integrations
- WorkItems
- approvals
- audit events
- employees
- departments
- knowledge
- products
- campaigns
- orders
- payments

must be scoped correctly by workspace.

If a proposed implementation could leak data between tenants, stop.

Do not continue until isolation is correct and tested.

---

# 17. Security and Permissions

For actions that can:

- send external messages
- modify customer data
- create financial effects
- delete data
- change configuration
- access sensitive information
- execute integrations

verify:

- employee permission
- capability authorization
- workspace ownership
- approval requirements
- audit logging

Do not allow convenience to bypass governance.

---

# 18. AI Cost Control

For AI-related tasks, always consider:

- Is an LLM call necessary?
- Can deterministic code handle it?
- Can a cheaper model handle it?
- Can the result be cached?
- Can calls be batched?
- Can repeated context be reduced?
- Does the task need a premium model?
- Is the call observable and attributable to a workspace?

Do not use AI where normal code is better.

Use stronger models only when they materially improve the result.

---

# 19. Dependency Rule

Do not install a dependency only because it makes one task easier.

Before adding a dependency, check:

- does Python/standard library already handle it?
- does the repository already include an equivalent?
- is it actively maintained?
- is the license acceptable?
- does it increase attack surface?
- does it create deployment complexity?
- will it be used enough to justify maintenance?

Prefer fewer dependencies.

If installation was not authorized in the current task, do not install anything.

---

# 20. No Unrelated Cleanup

While implementing a task:

Do not:

- rewrite unrelated files
- rename unrelated classes
- reorganize the project
- upgrade dependencies
- change formatting across the repository
- "improve" working architecture
- add speculative future features

unless explicitly required.

Unrelated cleanup makes testing, reviews, and debugging harder.

---

# 21. Git Discipline

When the roadmap requires one commit per task:

- start from a known clean checkpoint
- implement only the task
- run focused tests
- run full tests
- inspect `git diff`
- verify no secrets/temp files
- commit only the task
- use a clear commit message
- verify working tree state afterward

Do not push unless explicitly instructed.

Do not combine several numbered tasks into one commit unless explicitly requested.

---

# 22. Before Commit Checklist

Before committing:

- task acceptance criteria satisfied
- focused tests pass
- full suite passes
- no accidental debug code
- no secrets
- no `.env`
- no database files
- no cache files
- no unnecessary generated files
- no unrelated modifications
- workspace isolation preserved
- audit behavior correct
- permissions correct
- code matches HIRI architecture

Then commit.

---

# 23. Stop Conditions

Stop immediately when:

- a required test fails
- repository state differs from expected checkpoint
- architecture conflicts with the master plan
- tenant isolation is uncertain
- required data would be unsafe
- a dependency is required but not authorized
- task scope unexpectedly expands
- there are conflicting implementations
- a migration could destroy data
- secrets appear in tracked changes
- current branch/HEAD is not the expected one for a sensitive operation

Report the issue before continuing.

---

# 24. Fast Development Does Not Mean Rushed Development

The fastest route is usually:

correct architecture
+ small tasks
+ reuse
+ good prompts
+ focused tests
+ no unnecessary rewrites

The slowest route is usually:

large vague tasks
+ duplicate abstractions
+ repeated AI attempts
+ weak tests
+ architecture changes every week

Optimize for completed, stable functionality.

---

# 25. MVP Priority Rule

Until the first commercial HIRI version is validated, prioritize:

Generic HIRI foundation
→ Sales
→ real lead capture
→ real conversations
→ qualification
→ follow-up
→ approvals
→ audit
→ integrations
→ real customer tests

Do not let future departments delay the first working business outcome.

---

# 26. NOW / NEXT / LATER During Coding

Every new coding request should be tagged:

## NOW

Needed for current MVP or required technical foundation.

Implement when it reaches its roadmap position.

## NEXT

Useful after current MVP priorities.

Do not interrupt NOW tasks.

## LATER

Keep as an idea.

Do not implement yet.

ChatGPT should challenge scope creep when a LATER idea starts affecting NOW development.

---

# 27. Daily Development Loop

Recommended daily workflow:

1. Confirm current repository state.
2. Confirm next roadmap task.
3. Read task requirements.
4. Inspect relevant existing code.
5. Decide whether research is needed.
6. Decide implementation approach.
7. Choose manual / local implementation / Codex.
8. Implement one task.
9. Run focused tests.
10. Fix only task-related failures.
11. Run full suite.
12. Review diff.
13. Commit if required.
14. Record task status.
15. Start next task only after the checkpoint is clean.

---

# 28. How to Choose the Next Task

Do not choose tasks based on what looks interesting.

Choose the next task based on:

1. MVP dependency order
2. architectural prerequisites
3. customer value
4. risk reduction
5. integration dependency
6. testability
7. current roadmap sequence

If Task B depends on Task A, finish A first.

---

# 29. Documentation Rule

Maintain lightweight project documentation.

Important decisions should be reflected in:

- HIRI master plan
- current implementation status
- roadmap/task list
- ADR when an architectural decision deserves permanent explanation

Do not create excessive documentation for trivial changes.

Documentation should reduce future confusion.

---

# 30. Current Implementation Status File

`HIRI_CURRENT_IMPLEMENTATION_STATUS.md` should be updated at meaningful checkpoints.

It should contain:

- repository
- branch
- current HEAD
- origin status
- latest completed task
- current uncommitted task if any
- full-suite test baseline
- implemented HIRI components
- partially implemented components
- known gaps
- next approved task
- important technical constraints

This allows a new ChatGPT/Codex session to resume accurately without rereading months of conversation.

---

# 31. Session Handoff Rule

At the end of a significant coding session, record:

- tasks completed
- commits created
- tests
- working tree status
- ahead/behind
- current blockers
- next task
- anything that must not be modified

This is especially important when moving between:

- ChatGPT
- Codex
- Windows checkout
- another machine
- another coding session

Never rely only on conversational memory for repository state.

---

# 32. Prompt Efficiency

To save time and AI quota:

Do not repeatedly paste the entire HIRI history.

Instead provide the coding tool with:

- master constraints
- current implementation status
- specific task
- relevant code
- expected tests

Keep prompts precise.

More context is not always better.

Relevant context is better.

---

# 33. Debugging Workflow

When a test or runtime flow fails:

1. reproduce the failure
2. capture exact error
3. identify failing layer
4. inspect recent changes
5. inspect relevant logs
6. create the smallest hypothesis
7. test the hypothesis
8. apply the smallest fix
9. rerun focused test
10. rerun full suite

Do not perform random multi-file edits hoping the error disappears.

---

# 34. Integration Development Workflow

For external integrations such as Meta, WhatsApp, Gmail, CRMs, payments, etc.:

1. verify current official API documentation
2. decide whether this is an inbound HIRI API/webhook, an outbound provider adapter, or both
3. define the HIRI capability and WorkItem boundary
4. inspect and reuse the existing service, outbound-action and adapter architecture before creating anything new
5. define the provider adapter / service boundary
6. define credentials and secret-reference handling
7. define workspace ownership and tenant isolation
8. define AIEmployee / capability permissions
9. define approval requirements
10. define retry, idempotency, correlation and error behavior
11. define audit events and business-result capture
12. prefer direct/native Python provider adapters for required production paths
13. retain the generic webhook adapter for arbitrary systems and optional bridges
14. add background-worker infrastructure only when the integration has a demonstrated asynchronous or durable-execution requirement
15. if worker infrastructure is required, keep canonical state in HIRI/PostgreSQL and make the worker runtime replaceable
16. test with mocks and focused automated tests
17. test a real sandbox/dev account when available
18. keep provider-specific logic outside HIRI Core where possible

External systems connecting **to HIRI** must enter through authenticated, workspace-scoped HIRI API/webhook boundaries and must not bypass WorkItems, permissions, approvals or audit.

n8n is optional only. Do not make a production feature depend on n8n unless the task explicitly defines it as a temporary compatibility or integration bridge.

Do not allow provider APIs, Celery, n8n or another executor to dictate HIRI's business architecture.

# 35. Architecture Change Rule

Changing the architecture requires a stronger justification than adding a feature.

Before an architecture change, explain:

- current problem
- why existing architecture cannot solve it cleanly
- proposed change
- migration impact
- compatibility impact
- testing impact
- cost
- alternative approaches
- why the change is worth it

If the benefit is marginal, preserve the stable architecture.

---

# 36. Definition of Done for a Coding Task

A task is DONE only when:

- required behavior is implemented
- architecture remains correct
- tenant isolation is preserved
- permissions/approvals are handled where relevant
- focused tests pass
- full suite passes
- no unrelated changes exist
- code is reviewed
- task is committed when required
- status is recorded

"Code was generated" does not mean the task is done.

---

# 37. HIRI Development North Star

The development process should optimize for:

**Correctness
→ simplicity
→ reuse
→ security
→ testability
→ maintainability
→ speed
→ cost efficiency**

Speed matters, but not at the price of unstable architecture.

---

# 38. Instructions for ChatGPT

When helping develop HIRI:

- use `HIRI_MASTER_PLAN.md` as product/architecture source of truth
- use `HIRI_CURRENT_IMPLEMENTATION_STATUS.md` as repository-state source of truth
- use this file as development-process source of truth
- never invent repository status
- inspect actual code when available
- research current technical details when necessary
- prefer primary/official sources for technical decisions
- decide the simplest valid implementation
- preserve existing completed work
- prevent scope creep
- classify ideas NOW/NEXT/LATER
- conserve Codex quota
- prepare precise Codex prompts
- insist on focused + full tests
- stop on unexpected failures
- avoid unnecessary dependencies
- avoid unnecessary rewrites
- protect tenant isolation
- protect permissions and auditability
- keep AI cost under control
- finish one task before starting another

The goal is to build HIRI **faster by making fewer wrong moves**, not by producing code as quickly as possible.
---

# 39. Current Runtime and Documentation Authority

Current architecture decision — 2026-08-21:

- FastAPI remains the backend/domain owner.
- PostgreSQL/HIRI owns canonical business and workflow state.
- LangGraph handles AI reasoning/orchestration where required.
- Preserve existing HIRI business-service, outbound-action and DeliveryAdapter/provider-adapter execution boundaries.
- Direct/native provider adapters are preferred for required production integrations.
- The generic webhook boundary remains available for arbitrary systems and optional bridges.
- n8n is optional and replaceable; HIRI production must operate without it.
- Redis, Celery or another worker runtime is not an MVP prerequisite.
- Introduce worker infrastructure only when a demonstrated asynchronous or durable-execution requirement justifies it.
- Any future worker runtime must remain replaceable and must never own WorkItem, approval or business state.
- Temporal is LATER unless real durable-workflow complexity justifies it.

Source authority:

1. `docs/project/HIRI_MASTER_PLAN.md` — architecture/product direction.
2. `docs/project/HIRI_CURRENT_IMPLEMENTATION_STATUS.md` — verified repository state.
3. this workflow — development process.
4. `docs/project/HIRI_WEEKLY_EXECUTION_SYSTEM.md` — operational task tracking.
5. `docs/design/HIRI_UI_UX_DESIGN_SYSTEM.md` — frontend/UI/UX rules.
6. Git and tests — authoritative for what code actually exists.

If an older strategy document conflicts with the current Master Plan, do not code from the older document. Mark it superseded or archive it.
