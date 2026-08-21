# HIRI — MASTER PROJECT STRATEGY & ARCHITECTURE

## 1. Project Identity

HIRI is the single product/platform name.

Do not use or reintroduce previous marketing/product names such as:
- Selino
- Marivo
- Operio

Departments inside HIRI are not separate branded products.

HIRI is an **AI Workforce Platform / AI Company Operating System**.

HIRI allows a business to:
- hire one AI employee;
- hire multiple specialized AI employees;
- activate an entire AI department;
- combine multiple departments;
- connect AI employees to real business tools and communication channels;
- automate business work under permissions and human control;
- monitor performance, cost, activity and outcomes.

Long-term vision:

**A company should be able to operate significant parts of its business using coordinated AI employees through HIRI.**

HIRI is not simply:
- a chatbot;
- an automation builder;
- a CRM;
- an AI-agent demo;
- an n8n replacement;
- a ManyChat replacement;
- an ERP replacement.

HIRI is the intelligence and workforce layer that can use and coordinate these systems.

---

## 2. Permanent Core Architecture

Do not restart or replace this architecture without a major architectural reason.

The permanent HIRI foundation is:

Workspace / Tenant
→ Department
→ Department Supervisor
→ AIEmployee
→ Capabilities
→ Tools / Integrations / MCP
→ Permissions / Policies
→ WorkItems
→ Human Approvals
→ AI Gateway
→ Shared Business Data
→ Audit / Analytics

Every future feature should be fitted into this architecture whenever possible.

Do not create parallel architectures for individual features.

---

## 3. Workspace / Tenant

Each customer company receives an isolated HIRI Workspace.

A workspace owns its:
- users;
- departments;
- AI employees;
- capabilities;
- integrations;
- customers;
- leads;
- products;
- conversations;
- orders;
- campaigns;
- tickets;
- approvals;
- WorkItems;
- knowledge;
- AI usage;
- analytics;
- audit history;
- billing/configuration.

Strict tenant isolation is mandatory.

No workspace must ever access another workspace's data.

---

## 4. HIRI Departments

Long-term HIRI should support at least these major departments:

1. Sales
2. Marketing
3. Customer Support
4. Back Office / Operations
5. Finance Operations
6. HR / Recruitment

These represent the long-term company structure.

Do NOT build all departments at once.

The first commercial implementation should concentrate primarily on Sales while maintaining a generic architecture capable of supporting the others later.

---

## 5. Department Model

Each Department contains:
- Department Supervisor
- specialized AIEmployees
- department capabilities
- department-specific workflows
- permitted tools
- policies
- KPIs

Example:

HIRI Workspace
→ Sales Department
→ Sales Supervisor
→ Lead Capture AIEmployee
→ Qualification AIEmployee
→ Sales Conversation AIEmployee
→ Follow-up AIEmployee

Department Supervisors coordinate work rather than necessarily performing all work themselves.

Supervisor responsibilities:

Understand request
→ break work into WorkItems
→ select AIEmployee
→ determine capability
→ verify permissions
→ choose allowed tool
→ execute/orchestrate
→ evaluate result
→ assign next work

---

## 6. AIEmployee Model

AIEmployee is a generic reusable HIRI object.

Conceptually:

AIEmployee
- ID
- workspace
- department
- role
- instructions
- capabilities
- allowed tools
- permissions
- policies
- knowledge access
- model preferences
- autonomy level
- working context
- performance metrics
- status

Do not hardcode the entire platform around one fixed collection of agents.

Employees should be configurable through roles + capabilities + tools + permissions.

---

## 7. Capability Architecture

Capabilities define what an AIEmployee can do.

Examples:
- capture_lead
- qualify_lead
- research_company
- answer_customer
- send_email
- send_message
- schedule_meeting
- generate_proposal
- create_campaign
- create_social_content
- check_inventory
- create_invoice
- track_payment
- classify_ticket

Architecture:

AIEmployee
→ Required Capability
→ Allowed Tool / Integration
→ Action

Capabilities should remain independent from providers whenever possible.

---

## 8. Tools / Integrations / MCP

Integrations are tools used by HIRI employees.

Possible integrations include:
- Facebook
- Instagram
- Messenger
- WhatsApp
- Gmail
- Outlook
- Google Calendar
- Google Sheets
- HubSpot
- CRMs
- Shopify
- WooCommerce
- Slack
- Microsoft Teams
- payment providers
- accounting tools
- APIs
- webhooks
- MCP servers
- n8n

Target architecture:

WorkItem
→ Department Supervisor
→ Specialist AIEmployee
→ Required Capability
→ Allowed Tool / MCP / Integration

An AI employee must only receive access to the tools and data required for its role.

### 8A. HIRI External API / Integration Surface

HIRI is not only a consumer of external APIs. Other CRMs, company applications and business systems should be able to connect **to HIRI** through an HIRI-owned API and webhook boundary.

Target direction:

External CRM / company app
→ HIRI API / authenticated webhook
→ workspace validation
→ WorkItem / Capability
→ governed execution
→ Business Result
→ API response and/or outbound webhook
→ AuditEvent

Rules:
- every request is workspace-scoped and authenticated;
- external systems do not bypass WorkItems, permissions, approvals or audit;
- API contracts expose HIRI capabilities and business results, not internal executor details;
- provider-specific adapters remain outside HIRI Core;
- a broad public developer platform/SDK is **NEXT**, not required before the Sales MVP;
- only the API surface needed for real MVP channels/customers should be built NOW.

---

## 9. Execution and n8n Rule

Architecture decision — 2026-08-21.

HIRI production must be able to operate completely without n8n.

n8n may remain available as an optional development, compatibility or integration bridge, but it is not part of HIRI's required production runtime and must never become HIRI's workflow brain or source of truth.

HIRI / PostgreSQL owns:
- business logic;
- business and workflow state;
- WorkItems and their lifecycle;
- AI decisions and AIEmployee configuration;
- permissions and policies;
- approvals;
- scheduling state;
- retry and failure state where they are domain concerns;
- idempotency and correlation identifiers;
- audit history;
- business results.

FastAPI remains the backend and domain owner.

LangGraph remains the AI reasoning and orchestration layer where AI orchestration is required.

### Current Execution Boundary

Preserve and extend HIRI's existing execution architecture instead of creating a parallel runtime abstraction unnecessarily.

Preferred provider execution path:

WorkItem / business service
→ governed outbound action or service boundary
→ DeliveryAdapter / provider adapter
→ external API
→ result persisted and audited by HIRI

Direct native provider adapters are preferred for production integrations required by the HIRI Sales MVP.

The generic webhook adapter should remain available for arbitrary external systems and optional bridges such as n8n.

Also acceptable when useful:

HIRI
→ generic webhook adapter
→ optional n8n / external automation
→ result returns to HIRI

Incorrect:

n8n or another external executor owns HIRI business logic, canonical workflow state, permissions, approvals or business decisions.

### Background / Durable Execution

Redis, Celery or another worker runtime are NOT mandatory HIRI MVP infrastructure.

Introduce background-worker infrastructure only when a demonstrated workload requires execution outside the FastAPI request lifecycle, such as durable scheduling, significant asynchronous workloads or processing that must survive web-process restarts.

If such infrastructure is introduced:
- HIRI/PostgreSQL remains the canonical owner of WorkItem and business state;
- workers receive identifiers where practical and reload canonical state before execution;
- workspace ownership, permissions and current status are revalidated before actions execute;
- human approvals remain persisted HIRI state rather than workers waiting in memory;
- business services and AIEmployees must not depend directly on a specific queue vendor;
- the execution mechanism must remain replaceable behind an HIRI-owned boundary.

Redis + Celery may be evaluated as a pragmatic implementation when this requirement becomes real, but they are not a prerequisite for completing the Sales MVP.

Long-running scheduling should remain represented in HIRI state, for example through WorkItem waiting state and due-time fields, rather than depending on distant in-memory countdown jobs.

Temporal is LATER. Reconsider it only if real durable-workflow complexity materially exceeds the simpler HIRI-owned state and execution model.

Core rule:

HIRI decides and persists the work
→ an allowed execution boundary performs the action
→ external tools/providers execute only what HIRI permits
→ the result returns to HIRI
→ HIRI records the business result and audit history.

## 10. WorkItem Engine

WorkItem is the universal unit of work inside HIRI.

Possible lifecycle:

created
→ assigned
→ running
→ waiting
→ approval_required
→ completed

Alternative final states:
- failed
- cancelled
- expired

Departments should communicate through WorkItems rather than custom one-off orchestration whenever possible.

---

## 11. Human Approval and Autonomy

HIRI must support configurable autonomy.

### Level 1 — Suggest
AI recommends an action. Human executes it.

### Level 2 — Draft
AI prepares the action. Human approves execution.

### Level 3 — Controlled Automation
Low-risk permitted actions run automatically. Sensitive actions require approval.

### Level 4 — High Automation
Most permitted actions run automatically within strict policies.

Autonomy should be configurable by workspace, department, employee, capability and action.

---

## 12. Shared Business Data

HIRI departments should collaborate through shared business objects.

Core long-term objects include:
- Workspace
- User
- Department
- AIEmployee
- Capability
- Integration
- Customer
- Contact
- Lead
- Product
- Price
- Conversation
- Campaign
- Order
- Inventory
- Invoice
- Payment
- Ticket
- WorkItem
- Approval
- AuditEvent

Avoid building independent duplicate customer/lead/order systems inside each department.

---

## 13. Company Knowledge Layer

Each workspace should eventually maintain centralized company knowledge.

Possible knowledge:
- company information
- products
- pricing
- FAQs
- policies
- return rules
- delivery rules
- sales rules
- support procedures
- brand voice
- contracts
- internal documentation

AI employees only access knowledge allowed by their role and permissions.

---

## 14. AI Gateway

All AI model usage should eventually pass through the HIRI AI Gateway.

Architecture:

AIEmployee
→ AI Gateway
→ model routing
→ selected AI provider/model

AI Gateway responsibilities:
- provider abstraction
- model selection
- cost tracking
- token/usage tracking
- workspace quotas
- fallback models
- model policies
- observability
- retries
- logging
- budget controls

HIRI should not permanently depend on one LLM provider.

---

## 15. Local and Cloud Models

HIRI should support hybrid AI over time.

Possible providers:
- OpenAI
- Anthropic
- Gemini
- Groq
- other APIs
- local models through Ollama or similar infrastructure

Local GPU infrastructure is NOT an MVP requirement.

Initial production can primarily use APIs.

Later:
high-volume simple tasks → local/cheap models
complex reasoning → stronger cloud models

---

## 16. Sales Department — FIRST PRIORITY

Sales should be the first mature HIRI department.

Possible employees:
- Sales Supervisor
- Lead Capture Employee
- Lead Research Employee
- Qualification Employee
- Sales Conversation Employee
- Follow-up Employee
- Proposal Employee
- Sales Operations Employee

Core Sales flow:

Lead Capture
→ understand intent
→ create/match Lead
→ research if necessary
→ qualification
→ conversation
→ follow-up
→ approval where required
→ conversion / human handoff

---

## 17. Social Lead Capture

Social Lead Capture belongs inside Sales.

First implementation may include:

Facebook / Instagram comment
→ keyword or AI intent detection
→ automatic Messenger / Instagram DM
→ lead creation/matching
→ qualification
→ AI sales conversation
→ follow-up
→ human approval/takeover
→ conversion
→ audit

Future inbound sources should reuse the same Lead Capture architecture:
- WhatsApp
- website chat
- website forms
- email
- landing pages
- ads
- APIs
- other permitted social channels

Core principle:

**Capture → understand intent → qualify → converse → follow up → convert**

Do not recreate all of ManyChat.

---

## 18. Marketing Department

Marketing generates demand.

Possible employees:
- Marketing Supervisor
- Content Employee
- Campaign Employee
- Social Media Employee
- Market Research Employee
- SEO Employee
- Marketing Analytics Employee

Boundary:

Marketing generates attention and demand.

Once an individual prospect demonstrates buying intent, Sales owns lead capture and conversion.

---

## 19. Customer Support Department

Possible employees:
- Support Supervisor
- Customer Support Employee
- Technical Support Employee
- Customer Success Employee

Capabilities:
- customer questions
- FAQ
- ticket classification
- order status
- troubleshooting
- complaint handling
- escalation
- satisfaction follow-up

---

## 20. Back Office / Operations

Possible employees:
- Operations Supervisor
- Order Operations Employee
- Inventory Employee
- Delivery Employee
- Administrative Employee
- Document Employee

Capabilities:
- order processing
- inventory checking
- delivery management
- supplier coordination
- internal administration
- document workflows
- scheduling
- reporting

---

## 21. Finance Operations

Initially focus on operational finance support, not fully autonomous accounting.

Possible employees:
- Finance Supervisor
- Invoice Employee
- Payment Tracking Employee
- Expense Employee
- Financial Reporting Employee

High-risk financial actions require strict permissions and approvals.

---

## 22. HR / Recruitment

Later department.

Possible employees:
- HR Supervisor
- Recruitment Employee
- Candidate Screening Employee
- Onboarding Employee
- HR Operations Employee

Sensitive employment decisions must maintain human oversight.

---

## 23. Audit Architecture

Important actions should create AuditEvents.

HIRI should eventually be able to answer:
- What did the AI do?
- Why?
- Which tool did it use?
- Which employee performed it?
- Was it approved?
- What happened afterward?

Auditability is a core platform feature.

---

## 24. Analytics

Two categories:

### Business Analytics
Examples:
- leads
- conversion rate
- revenue
- campaign results
- support performance
- orders
- payments

### AI Workforce Analytics
Examples:
- WorkItems completed
- employee performance
- success/failure rates
- human interventions
- automation percentage
- tool usage
- model usage
- AI cost
- cost per outcome

Long-term HIRI should answer:

**What work did my AI workforce accomplish today, what did it cost, and what business result did it generate?**

---

## 25. HIRI Product Interface

Recommended high-level navigation:
- Home
- Workforce
- Departments
- Work
- Customers
- Inbox
- Knowledge
- Integrations
- Approvals
- Analytics
- Settings

### 25A. Official HIRI UI/UX Design System

All frontend work must follow `docs/design/HIRI_UI_UX_DESIGN_SYSTEM.md` as the authoritative detailed UI/UX specification.

Core rules:
- professional enterprise product design with HIRI's own identity;
- restrained spacing, borders and corner radius;
- strong information hierarchy;
- tables, lists and operational panels where appropriate;
- reusable components and subtle purposeful motion;
- no generic purple-AI gradients;
- no glassmorphism everywhere;
- no excessive cards, giant rounded corners or random glow;
- no generic robot/brain graphics;
- marketing illustrations must reinforce HIRI's business-workforce concept and use the approved HIRI brand assets;
- important product copy must remain editable HTML/UI text rather than being baked into artwork.

No n8n-like visual workflow builder is required for the MVP. Customers interact with AI employees, WorkItems, approvals, inbox, analytics, integrations and configuration—not HIRI's internal execution plumbing.

---

## 26. Hiring an AI Employee

Long-term user experience:

Hire AI Employee
→ choose Department
→ choose Role
→ choose Capabilities
→ connect Tools
→ configure Permissions
→ configure Autonomy
→ choose Knowledge access
→ activate

---

## 27. Department Templates

HIRI should eventually offer department templates.

Example:

Add Sales Department

HIRI automatically provisions:
- Sales Supervisor
- Lead Capture
- Lead Research
- Qualification
- Sales Conversation
- Follow-up

Templates provide easy onboarding while keeping the underlying AIEmployee architecture generic.

Marketplace functionality is LATER, not MVP.

---

## 28. Open-Source Repository Rule

Open-source repositories are resources, NOT HIRI's foundation.

Every discovered repository must be classified:

A. Reuse Code
B. Reuse Pattern
C. Integration
D. Ignore

Do not redesign HIRI every time an interesting GitHub project is discovered.

Treat `Shubhamsaboo/awesome-llm-apps` as a source of reusable AI-agent patterns/components, not as HIRI's architecture.

---

## 29. Current Technical Direction

Keep the existing working technical direction unless a demonstrated requirement justifies change.

Backend:
- Python
- FastAPI as the backend and domain owner

AI orchestration:
- LangGraph where AI reasoning or multi-step AI orchestration is required
- deterministic application services for normal business rules and execution

Database:
- SQLite where appropriate for development and tests
- PostgreSQL for production and canonical business/workflow state

Execution:
- preserve existing HIRI service and outbound-action execution boundaries
- use DeliveryAdapter / provider-adapter architecture for external actions
- direct native Python adapters are preferred for production MVP channels
- background-worker infrastructure is added only when a demonstrated workload requires it
- any future queue/worker runtime must remain replaceable and must not own HIRI business state

Integrations:
- direct/native Python API adapters
- authenticated HIRI API and webhook boundary
- generic webhook adapter
- MCP where useful
- n8n only as an optional replaceable compatibility/integration bridge

Secrets:
- provider credentials must not be persisted directly in business payloads
- persist safe secret references and resolve credential values through an approved secret boundary

Deployment:
- Docker
- VPS initially
- HTTPS
- PostgreSQL
- backups
- monitoring
- additional worker/queue infrastructure only when operationally required

FastAPI remains the business/domain owner.

PostgreSQL/HIRI remains the source of truth for business and workflow state.

LangGraph remains the AI orchestration layer.

n8n remains optional and replaceable.

Redis, Celery or another worker runtime may be introduced later when justified by real asynchronous or durable-execution requirements; they are not prerequisites for the Sales MVP.

Temporal is LATER and should only be introduced when demonstrated workflow complexity justifies the additional runtime.

## 30. Logical Backend Layers

Target conceptual backend structure:

API Layer
→ Domain / Business Services
→ Workforce / Department Coordination
→ WorkItem Engine
→ Approval / Policy Enforcement
→ Capability Execution
→ AI Orchestration through LangGraph where required
→ Tool / Integration / DeliveryAdapter Layer
→ Data Layer
→ Audit / Analytics

Background or durable execution infrastructure may be inserted behind HIRI-owned service boundaries when required, but it is not a mandatory architectural layer for every WorkItem or integration.

Preserve existing working services and adapters instead of introducing duplicate execution abstractions.

## 31. HIRI MVP Strategy

Do not attempt to launch every department.

The first commercial HIRI release should prove the generic AI Workforce architecture through an excellent Sales department used by real businesses.

This section defines MVP capability scope. Actual implementation state belongs in HIRI_CURRENT_IMPLEMENTATION_STATUS.md and must be verified against Git and tests.

### Platform Foundation
- Workspace / tenant isolation
- authentication and workspace membership
- Department and Department Supervisor
- generic AIEmployee model
- Capabilities and assignments
- allowed tools / integrations
- permissions and policies
- configurable autonomy

### Governed Work Execution
- WorkItems as the universal work unit
- assignment and lifecycle management
- approval gates for sensitive actions
- business results
- audit history
- retries, idempotency and correlation where required
- existing service and adapter execution boundaries

Background-worker infrastructure is not an MVP requirement unless a real workload demonstrates that it is necessary.

### Sales
- Lead Capture
- intent understanding
- lead creation / matching
- Qualification
- Sales Conversation
- Follow-up
- human takeover / escalation
- conversion-oriented workflow

### Shared Business Data
- leads
- customers / contacts
- products and prices
- conversations

### MVP Channels and Integrations
- HIRI API / authenticated webhook boundary where needed
- Facebook / Instagram lead capture
- WhatsApp Cloud when the direct production path is ready
- website/API channels where required by real pilot customers
- direct provider adapters preferred for required production channels
- generic webhook compatibility retained

Do not build dozens of integrations before real customer demand justifies them.

### AI
- AI Gateway / provider abstraction
- model routing
- usage and cost tracking
- workspace limits / budget controls
- LangGraph where AI orchestration is actually needed

### UI
- Workforce
- WorkItems
- Approvals
- Inbox / conversations
- customers / leads
- integrations
- basic business and AI Workforce analytics

### MVP Success Condition

The MVP is successful when a real business can connect a real Sales channel, allow HIRI AI employees to capture and work leads under workspace permissions and approval rules, complete useful Sales WorkItems, communicate with prospects, follow up, and produce auditable business results with controlled AI cost.

The MVP must validate real business value before HIRI expands aggressively into additional departments or speculative infrastructure.

## 32. Roadmap

This roadmap defines priority and direction. It is not the authoritative record of what is already implemented.

Verified implementation state belongs in HIRI_CURRENT_IMPLEMENTATION_STATUS.md. Existing stable foundations must not be recreated simply because they appear in the architectural scope.

### Established Foundation — Preserve, Do Not Rebuild
- Workspace / tenant isolation and workspace RBAC
- Department and Department Supervisor foundations
- generic AIEmployee and Capability foundations
- AIEmployee capability assignment and tool-access governance
- WorkItems and approval linkage
- Sales orchestration and follow-up execution
- AI Gateway / model routing / usage and cost controls
- integration-account, outbound-action, retry, idempotency and audit foundations
- Sales lead capture and social lead capture foundations
- operator Workforce / WorkItem / Approval / Analytics UI
- HIRI public and authenticated frontend foundations

### NOW — Finish the First Useful HIRI Sales MVP
- complete the documentation and implementation-status cleanup gate
- close remaining gaps required for direct production Sales channels
- add generic credential-reference support where provider adapters require multiple external credentials
- complete the direct WhatsApp Cloud outbound provider-adapter path
- complete the direct WhatsApp Cloud inbound provider boundary so n8n is not required
- preserve the existing generic webhook / optional n8n compatibility path
- verify the complete Sales flow through real communication channels
- fix any remaining gaps in Capture → Understand → Qualify → Converse → Follow up → Convert
- validate permissions, approvals, tenant isolation, AI cost controls and audit behavior end to end
- test HIRI with real pilot businesses and use observed gaps to determine subsequent NOW tasks

Do not introduce Redis, Celery, Temporal, additional departments or broad integration infrastructure merely to complete this list. Add infrastructure only when a demonstrated requirement needs it.

### NEXT
- expand the HIRI external API / developer surface where customer demand requires it
- Marketing department
- Customer Support department
- stronger omnichannel inbox
- richer AIEmployee configuration
- improved company knowledge system
- additional high-value integrations
- stronger business and AI Workforce analytics
- cross-department WorkItem coordination

### LATER
- complete Back Office / Operations
- complete Finance Operations
- complete HR / Recruitment
- AIEmployee and department marketplaces
- broad MCP ecosystem
- advanced enterprise administration
- large GPU infrastructure
- complex local-model infrastructure
- Temporal or comparable durable-workflow infrastructure if real complexity justifies it
- dozens of integrations without validated customer demand

Roadmap rule:

Finish and validate the Sales department before horizontally expanding HIRI.

## 33. First Target Customer

Initial target:

Small and medium businesses that receive online leads/messages and currently handle sales manually.

Potential sectors:
- e-commerce
- tourism
- real estate
- agencies
- services
- training/education
- local businesses

Initial value:

Capture lead
→ understand intent
→ qualify
→ respond
→ follow up
→ human approval where needed
→ convert
→ track results

---

## 34. What HIRI Must NOT Become

HIRI should NOT become:
- Zapier clone
- n8n clone
- visual workflow-builder clone
- ManyChat clone
- HubSpot clone
- ERP clone
- Shopify clone
- accounting software clone
- generic ChatGPT clone

Instead:

HIRI AI Workforce
→ decides and coordinates business work
→ uses specialized capabilities
→ uses existing tools/integrations
→ keeps business state and governance inside HIRI

---

## 35. New Idea Decision Rule

Every future idea must be evaluated before being added.

Ask:

1. Which HIRI department does it belong to?
2. Which AIEmployee needs it?
3. Is it a capability, tool, workflow or shared platform feature?
4. Do we already have something equivalent?
5. Is it NOW, NEXT or LATER?
6. Does it genuinely require changing the core architecture?
7. Can we reuse existing HIRI concepts instead?
8. Does it add unnecessary complexity?
9. Does it create unnecessary infrastructure/API cost?
10. Does it help the current MVP or distract from it?

If an idea cannot yet justify its place, place it in the idea parking lot instead of development.

---

## 36. Core Development Rule

Do not expand HIRI horizontally until the foundation and first department work properly.

Development priority:

Generic HIRI Foundation
→ excellent Sales Department
→ real communication channels
→ real businesses
→ validate results
→ Marketing / Support
→ cross-department automation
→ additional departments
→ advanced AI Company OS

---

## 37. Architectural North Star

Workspace
→ Department
→ Department Supervisor
→ AIEmployee
→ Capability
→ Allowed Tool
→ WorkItem Execution
→ Approval if required
→ Business Result
→ Audit + Analytics

Cross-department workflows share company data and WorkItems.

---

## 38. Canonical HIRI Definition

**HIRI is an AI Workforce Platform that lets businesses deploy coordinated AI employees and departments to perform real business work through controlled capabilities and integrations, while sharing company data and operating under permissions, human approvals, AI cost controls and full auditability.**

Short version:

**HIRI — AI Workforce for Business**

---

## 39. Instructions for ChatGPT Working on This Project

When working on HIRI:

- Treat this Master Plan as the architectural and product source of truth.
- Preserve the existing Generic AI Workforce architecture.
- Do not restart or replace stable HIRI architecture without a demonstrated benefit.
- Use HIRI as the single product/platform name.
- Do not reintroduce old department/product brand names.
- Reuse existing code, services, models and concepts before creating new abstractions.
- Protect strict workspace / tenant isolation.
- Keep FastAPI as the backend and domain owner.
- Keep PostgreSQL / HIRI as the canonical owner of business and workflow state.
- Keep LangGraph as the AI orchestration layer where AI orchestration is required.
- Prefer deterministic application services for normal business rules and execution.
- Preserve existing business-service, outbound-action and DeliveryAdapter/provider-adapter boundaries.
- Prefer direct native provider adapters for required production integrations.
- Keep n8n optional and replaceable; HIRI must operate without it.
- Do not introduce Redis, Celery or another worker runtime unless a demonstrated asynchronous or durable-execution requirement justifies it.
- If worker infrastructure is introduced, keep it replaceable and never allow it to own HIRI business state.
- Keep Temporal classified as LATER unless real workflow complexity justifies it.
- Treat MCP servers and integrations as tools, not HIRI's architecture.
- Prefer configurable AIEmployees and capabilities over hardcoded one-off agents.
- Prefer WorkItems as the universal unit of governed work.
- Require permissions, policies and approval for sensitive actions.
- Route AI usage through the AI Gateway architecture.
- Track AI usage, cost, results and audit history.
- Do not start new departments merely because an interesting repository or framework is discovered.
- Classify new ideas into NOW / NEXT / LATER.
- Classify open-source repositories as Reuse Code / Reuse Pattern / Integration / Ignore.
- Prioritize completing and validating the Sales MVP before horizontal expansion.
- Challenge unnecessary complexity, duplicate concepts and premature infrastructure.
- When suggesting an architecture change, explain exactly where it fits in the existing HIRI model.

The goal is not to accumulate AI features.

The goal is to build one coherent, scalable AI Workforce platform called HIRI.

## 40. Documentation Source-of-Truth and Supersession Rule

HIRI documentation must have one clear source-of-truth hierarchy.

1. docs/project/HIRI_MASTER_PLAN.md — product vision, permanent architecture, technical direction, MVP boundaries and NOW / NEXT / LATER.
2. docs/project/HIRI_CURRENT_IMPLEMENTATION_STATUS.md — verified repository state. Git and tests are authoritative for what actually exists.
3. docs/project/HIRI_DEVELOPMENT_WORKFLOW.md — development, testing, review and coding-agent process.
4. docs/project/HIRI_WEEKLY_EXECUTION_SYSTEM.md — operational planning and active execution rules.
5. docs/design/HIRI_UI_UX_DESIGN_SYSTEM.md — authoritative frontend and UI/UX implementation specification.
6. ADRs — durable technical decisions and historical architectural rationale.

Conflict rules:
- when an older strategy document conflicts with this Master Plan, the current Master Plan wins;
- when conversation memory or planning documents conflict with verified Git/test state about implementation, Git and tests win;
- newer architectural decisions incorporated into this Master Plan supersede older architecture notes;
- historical acceptance documents and superseded ADRs may remain for evidence and rationale but must not be treated as current architecture;
- old master plans and strategy copies must be archived or clearly marked SUPERSEDED;
- do not maintain competing active roadmaps;
- the weekly execution system is operational planning; the Master Plan defines product and architectural direction.

### Superseded Runtime Guidance

Older Taskiq / Valkey proposals and designs that require n8n as the production runtime are not the current HIRI direction.

Current rule:
- HIRI / PostgreSQL owns canonical business and workflow state;
- FastAPI remains the backend and domain owner;
- existing HIRI business-service, outbound-action and DeliveryAdapter/provider-adapter boundaries should be preserved and extended;
- direct provider adapters are preferred for required production integrations;
- the generic webhook boundary remains available;
- n8n is optional compatibility or integration infrastructure only;
- Redis, Celery or another worker runtime may be introduced when a demonstrated asynchronous or durable-execution requirement justifies it, but none is an MVP prerequisite;
- any future worker runtime must remain replaceable and must not own HIRI business state;
- Temporal remains LATER unless real workflow complexity demonstrates the need for it.
