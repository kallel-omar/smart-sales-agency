# HIRI — WEEKLY EXECUTION & AUTOMATED TASK LIST SYSTEM

## Purpose

This document defines how HIRI development work should be organized, tracked, and reviewed week by week.

It complements:

- `HIRI_MASTER_PLAN.md` → what HIRI is
- `HIRI_DEVELOPMENT_WORKFLOW.md` → how HIRI is coded
- `HIRI_CURRENT_IMPLEMENTATION_STATUS.md` → where the repository is now
- this file → how weekly tasks are planned, tracked, updated, and closed

The goal is to maintain one reliable development task list that reflects the real repository state.

Before weekly planning begins, documentation must be clean:
- `HIRI_MASTER_PLAN.md` is the current architecture/product source of truth;
- `HIRI_CURRENT_IMPLEMENTATION_STATUS.md` must reflect verified Git/test state;
- older conflicting strategy/master copies must be marked superseded or archived;
- the Google Sheet must not be populated from stale roadmap assumptions.

---

# 1. One Source of Truth for Tasks

Use one Google Sheet as the active HIRI task board.

Do not maintain several competing task lists.

The Sheet should represent the real development state:

Backlog
→ Ready
→ In Progress
→ Testing
→ Done

Additional status:

- Blocked
- Deferred
- Cancelled

A task must not be marked Done until its required tests and completion checks pass.

---

# 2. Weekly Planning Rule

### Cleanup gate

Do not create the active weekly queue until:
1. the current Master Plan is canonical;
2. current Git/working-tree state is verified;
3. `HIRI_CURRENT_IMPLEMENTATION_STATUS.md` is updated;
4. repository gaps are classified as Implemented / Partial / Missing / NEXT / LATER;
5. the next milestone dependency chain is known.

At the beginning of each development week:

1. Read `HIRI_MASTER_PLAN.md`.
2. Read `HIRI_CURRENT_IMPLEMENTATION_STATUS.md`.
3. Confirm the current Git branch / HEAD / test baseline.
4. Review unfinished tasks from the previous week.
5. Select only the tasks required for the next meaningful milestone.
6. Put them in dependency order.
7. Mark each task as NOW, NEXT, or LATER.
8. Only NOW tasks enter the active weekly queue.

Do not fill the week with speculative features.

---

# 3. Recommended Google Sheet Columns

Create these columns:

| Column | Purpose |
|---|---|
| Task ID | Permanent task number |
| Week | Week / sprint identifier |
| Phase | NOW / NEXT / LATER |
| Department | Sales / Platform / Marketing / etc. |
| Area | Workforce / WorkItems / Integration / Audit / etc. |
| Task | Short task title |
| Description | Exact implementation objective |
| Depends On | Previous task IDs required first |
| Priority | Critical / High / Medium / Low |
| Risk | High / Medium / Low |
| Tool | ChatGPT / Codex / Manual / Research |
| Status | Backlog / Ready / In Progress / Testing / Done / Blocked |
| Focused Tests | Not Run / Pass / Fail |
| Full Suite | Not Run / Pass / Fail |
| Commit | Git commit hash |
| Started | Start date |
| Completed | Completion date |
| Blocker | Current blocker if any |
| Notes | Short technical notes |

Optional later columns:

- Estimated effort
- Actual effort
- PR
- Release
- Customer value
- AI/API cost impact
- Security impact
- Tenant-isolation impact

---

# 4. Status Meaning

## Backlog

Task exists but is not approved for current execution.

## Ready

Task is approved and all required dependencies are complete.

## In Progress

Implementation has started.

Only a very small number of tasks should be In Progress at once.

## Testing

Implementation is complete and verification is running.

## Done

Required behavior is implemented and verified.

Typical conditions:

- focused tests pass
- full suite passes
- diff reviewed
- no unrelated changes
- commit recorded when required
- repository state is known

## Blocked

The task cannot continue because of:

- test failure
- architectural conflict
- missing dependency
- missing credentials
- external API issue
- unexpected repository state
- decision required

The blocker must be written in the Sheet.

---

# 5. Daily Task Flow

Use this flow every development session:

Open HIRI task Sheet
→ verify current repository state
→ select first Ready task
→ confirm scope
→ inspect existing code
→ research if necessary
→ choose implementation method
→ mark In Progress
→ implement
→ run focused tests
→ mark Testing
→ run full suite
→ review diff
→ commit when required
→ mark Done
→ record commit + tests
→ refresh implementation status
→ select next Ready task

Do not skip directly to a later task.

---

# 6. Task Selection Rule

The next task should be the first Ready task with the highest dependency priority.

Choose tasks based on:

1. prerequisite order
2. MVP importance
3. architectural dependency
4. customer value
5. risk reduction
6. testability

Do not choose the next task because it looks more interesting.

---

# 7. One Task at a Time

Each numbered task should have one main purpose.

Good:

`Task 301 — Add workspace-scoped capability assignment`

Bad:

`Task 301 — Build capabilities, redesign permissions, add dashboard, integrate Meta and refactor WorkItems`

Large ideas should be split into sequential tasks.

---

# 8. Task Definition Template

Every task should contain:

## Goal

One clear result.

## Why

Why HIRI needs it now.

## Scope

Exact functionality to implement.

## Preserve

Existing behavior that must not change.

## Dependencies

Previous required tasks.

## Acceptance Criteria

Observable conditions proving completion.

## Focused Tests

Tests that directly validate the feature.

## Full Suite

Full repository test command.

## Stop Conditions

Conditions that require stopping rather than continuing.

---

# 9. Automated Sheet Update Logic

The Google Sheet should be updated from real development checkpoints, not assumptions.

Recommended automation behavior:

### When a task starts

Update:

- Status → In Progress
- Started → current date

### When focused tests pass

Update:

- Focused Tests → Pass
- Status → Testing

### When focused tests fail

Update:

- Focused Tests → Fail
- Status → Blocked
- Blocker → short failure summary

### When full suite passes

Update:

- Full Suite → Pass

### When full suite fails

Update:

- Full Suite → Fail
- Status → Blocked
- Blocker → failing test / reason

### When task commit is created

Update:

- Commit → commit hash
- Status → Done
- Completed → current date

Only mark Done after the real repository confirms completion.

---

# 10. Automation Architecture for the Task Sheet

Keep the automation simple.

Recommended flow:

HIRI development checkpoint
→ structured task result
→ Google Sheets update
→ weekly dashboard

The task result should contain fields such as:

- task_id
- status
- focused_tests
- full_suite
- commit
- blocker
- notes

The Sheet should not decide whether code is correct.

Git + tests remain the source of truth.

The Sheet records that truth.

---

# 11. Weekly Dashboard

Create a summary tab with:

- total weekly tasks
- Done
- In Progress
- Ready
- Blocked
- completion percentage
- tests passing
- tasks by department
- tasks by area
- high-risk tasks remaining
- commits completed

Optional:

- planned vs completed
- average task duration
- blocked-task count
- Codex-heavy tasks
- customer-value milestones

---

# 12. Weekly Review

At the end of the week:

1. Count completed tasks.
2. Verify all Done tasks have valid commits/tests.
3. Review Blocked tasks.
4. Move unfinished valid tasks to the next week.
5. Remove tasks that are no longer necessary.
6. Compare progress against the HIRI MVP.
7. Update `HIRI_CURRENT_IMPLEMENTATION_STATUS.md`.
8. Choose the next weekly objective.
9. Generate the next week's Ready queue.

Do not automatically carry every backlog task forward.

---

# 13. Weekly Objective Rule

Each week should have one primary objective.

Example:

**Objective: Complete Social Lead Capture foundation**

Possible tasks:

- Trigger model
- Meta integration boundary
- inbound event normalization
- lead matching
- WorkItem creation
- audit events
- focused tests
- integration tests

Another example:

**Objective: Complete AIEmployee capability permissions**

This keeps weekly work coherent.

---

# 14. NOW / NEXT / LATER Integration

The Sheet should use the same product priorities as the HIRI Master Plan.

## NOW

Eligible for active weekly work.

## NEXT

May be planned but should normally remain outside the current weekly queue.

## LATER

Parking-lot idea.

Never let LATER tasks silently become active development without an explicit decision.

---

# 15. Blocked Task Rule

When a task becomes Blocked:

1. stop implementation
2. record exact blocker
3. preserve repository state
4. do not start dependent tasks
5. decide whether the blocker needs:
   - research
   - architecture decision
   - credentials
   - bug fix
   - external service resolution
6. unblock deliberately
7. resume the same task

Do not hide blockers by changing task definitions.

---

# 16. Task Numbering

Keep permanent sequential task IDs **only after the last real historical task ID has been verified**.

Do not invent a new permanent task number from conversation memory or from an example in this document.

During a cleanup/audit where the last permanent ID is unknown:
- keep candidate work unnumbered or use temporary labels such as `TMP-A`, `TMP-B`;
- verify task history from the repository/Sheet;
- assign permanent sequential IDs only after the last valid ID is known.

Once numbering is verified, do not reuse old task numbers.

If a task is cancelled, keep the number and mark it Cancelled.

This preserves development history without fabricating history.

---

# 17. Git Relationship

The Sheet and Git must agree.

For a completed task, ideally store:

- task ID
- commit hash
- full-suite result

Example:

Task 301
Status: Done
Focused Tests: Pass
Full Suite: 412 passed
Commit: `abc1234`

If the Sheet says Done but no required commit/test exists, the task is not truly complete.

---

# 18. ChatGPT Role in Weekly Planning

Use ChatGPT to:

- review repository status
- compare progress to the HIRI Master Plan
- choose weekly objectives
- break objectives into tasks
- identify dependencies
- identify risks
- prepare Codex prompts
- review blockers
- decide the next Ready task
- prepare weekly review summaries

ChatGPT should not invent completed work.

Repository state and tests remain authoritative.

---

# 19. Codex Task Planning

Before spending Codex quota:

1. task must already exist in the Sheet
2. task status should be Ready
3. scope must be precise
4. dependencies must be satisfied
5. relevant architecture must be known
6. tests must be defined

After Codex finishes:

- inspect result
- run required tests
- record actual result
- update Sheet

Do not mark a Codex task Done just because Codex says it completed the code.

---

# 20. Research Task Type

Some tasks may be research-only.

Example:

`Task 315 — Verify current Meta Instagram comment webhook requirements`

Research tasks should produce:

- source links
- decision
- architectural impact
- recommended implementation
- whether a coding task is needed

A research task can be Done without a code commit if its acceptance criteria are satisfied.

---

# 21. Example Weekly Task Queue — ILLUSTRATIVE ONLY

| Task ID | Phase | Area | Task | Priority | Status |
|---|---|---|---|---|---|
| EXAMPLE-301 | NOW | Platform | Capability permission model | Critical | Ready |
| EXAMPLE-302 | NOW | Platform | Capability assignment API | High | Backlog |
| EXAMPLE-303 | NOW | Sales | Lead Capture trigger model | High | Backlog |
| EXAMPLE-304 | NOW | Sales | Inbound event normalization | High | Backlog |
| EXAMPLE-305 | NEXT | Marketing | Campaign employee template | Medium | Backlog |
| EXAMPLE-306 | LATER | Marketplace | Employee marketplace | Low | Backlog |

Only tasks whose dependencies are satisfied should become Ready.

---

# 22. Recommended Tabs

Use these Google Sheet tabs:

## 1. Active Tasks

Current weekly execution queue.

## 2. Backlog

Future approved tasks.

## 3. Parking Lot

NEXT/LATER ideas not ready for development.

## 4. Weekly Summary

Weekly metrics and progress.

## 5. Completed

Historical completed tasks with commits/tests.

Optional:

## 6. Decisions

Important architectural decisions / blockers / research outcomes.

---

# 23. Automation Safety

Do not allow task automation to:

- change source code automatically without the coding workflow
- mark tasks Done without verification
- push Git commits automatically unless explicitly desired
- modify architecture
- reorder dependencies silently
- delete task history
- overwrite blocker notes

Automation should reduce administration, not control engineering decisions.

---

# 24. End-of-Task Update Format

Use a structured result like:

Task: 301
Status: Done
Focused Tests: Pass
Full Suite: Pass
Commit: abc1234
Working Tree: Clean
Blocker: None
Notes: Added workspace-scoped capability assignment.

This makes Sheet updates reliable.

---

# 25. End-of-Day Checkpoint

Before stopping development:

- current task status is accurate
- repository state is known
- uncommitted work is documented
- test state is recorded
- blocker is recorded if present
- next Ready task is visible
- Sheet matches reality

Never leave the Sheet showing Done when the repository contains unfinished work.

---

# 26. End-of-Week Checkpoint

Record:

- weekly objective
- tasks completed
- commits
- full-suite baseline
- blockers
- tasks carried forward
- MVP progress
- next week's objective

Update `HIRI_CURRENT_IMPLEMENTATION_STATUS.md` if the repository reached a meaningful new checkpoint.

---

# 27. Core Rule

The Google Sheet is the operational dashboard.

The Git repository is the technical source of truth.

The HIRI Master Plan is the strategic source of truth.

These three must remain aligned.

---

# 28. Final Workflow

HIRI Master Plan
→ Current Implementation Status
→ Weekly Objective
→ Ordered Task List
→ First Ready Task
→ Development Workflow
→ Tests
→ Commit
→ Automated Sheet Update
→ Next Task
→ Weekly Review

This system should make HIRI development measurable, recoverable, and easy to continue across ChatGPT/Codex sessions without losing track of what is actually complete.
