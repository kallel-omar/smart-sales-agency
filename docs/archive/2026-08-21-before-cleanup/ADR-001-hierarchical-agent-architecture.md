# ADR-001: Hierarchical Multi-Agent Architecture

## Status

Accepted

## Context

The platform is evolving from a Multi-Agent Smart Sales Agency into an AI Business Operating System.

The long-term platform will contain three AI departments:

1. Sales Department
2. Marketing Department
3. Back-Office Department

The departments will share business data such as workspaces, products, customers, conversations, campaigns, approvals, orders, inventory, payments, deliveries, documents, and analytics.

A flat system in which every agent communicates freely with every other agent would be difficult to secure, test, audit, control, and scale.

The platform must also control AI cost. It must not activate every agent or use an expensive model for every customer message.

## Decision

The platform will use a hierarchical multi-agent architecture.

```text
Business Supervisor
├── Sales Department Supervisor
├── Marketing Department Supervisor
└── Back-Office Department Supervisor
```

The architecture will contain three orchestration levels.

## Level 1 — Business Supervisor

The Business Supervisor coordinates complete business operations involving multiple departments.

Responsibilities:

- Receive business goals and cross-department events
- Determine which departments are required
- Coordinate dependencies between departments
- Enforce global priorities, budgets, policies, and approvals
- Track complete business-operation status
- Escalate failures and high-risk decisions
- Produce consolidated business outcomes

The Business Supervisor must not directly:

- Send customer messages
- Create marketing content
- Change inventory
- Process payments
- Modify orders
- Execute specialist tools

The Business Supervisor should not process every normal customer message.

Simple departmental operations must bypass it when possible.

```text
Normal sales question
→ Deterministic router
→ Sales Department

Cross-department business goal
→ Business Supervisor
→ Required departments
```

## Level 2 — Department Supervisors

The platform will contain:

- Sales Department Supervisor
- Marketing Department Supervisor
- Back-Office Department Supervisor

Each department supervisor will:

- Receive structured departmental tasks
- Select the correct departmental workflow
- Coordinate specialist agents
- Validate outputs
- Retry, pause, escalate, or complete execution
- Report structured results
- Respect workspace budgets, permissions, and policies

Each department will own separate workflows rather than sharing one enormous agent graph.

```text
Business orchestration
├── Sales workflows
├── Marketing workflows
└── Back-Office workflows
```

## Level 3 — Specialist Agents

Specialist agents perform narrow reasoning or generation tasks.

### Sales Department

- Lead Research Agent
- Qualification Agent
- Sales Conversation Agent
- Follow-up Agent
- Negotiation Agent
- Sales Analytics Agent

### Marketing Department

- Market Research Agent
- Campaign Strategy Agent
- Content Agent
- Social Media Agent
- Audience Segmentation Agent
- Marketing Analytics Agent

### Back-Office Department

- Order Operations Agent
- Inventory Agent
- Payment Agent
- Delivery Agent
- Document Agent
- Operations Analytics Agent

Each specialist agent must have:

- A narrow responsibility
- Typed input and output schemas
- Explicitly allowed tools
- Workspace-scoped permissions
- Token and cost limits
- Timeout and retry policies
- Evaluation tests
- Audit information

## Deterministic Routing

The platform should use deterministic routing whenever the destination is already clear.

Examples:

```text
Inbound customer message
→ Sales Department

Campaign performance request
→ Marketing Department

Stock or delivery event
→ Back-Office Department
```

The Business Supervisor should activate only when:

- More than one department is required
- A department cannot complete the operation
- A global business decision is required
- Budget or policy escalation is required
- A cross-department dependency exists

## Shared Control Plane

Security and operational controls must not be implemented only inside supervisors.

The platform will contain shared control-plane services:

```text
Control Plane
├── Authentication and workspace security
├── Memberships, roles, and permissions
├── Policy and approval engine
├── Model router
├── AI budget and quota manager
├── Tool permission gateway
├── Execution tracking
├── Audit logging
└── Observability
```

All departments and supervisors must use these shared services.

## Structured Communication

Departments and agents must communicate through typed contracts and business events rather than unrestricted natural-language messages.

Example events:

- LeadGenerated
- LeadQualified
- CustomerInterested
- CampaignLaunched
- OrderRequested
- StockReserved
- PaymentConfirmed
- DeliveryScheduled
- RefundRequested

Every event should include:

- `event_id`
- `workspace_id`
- `correlation_id`
- `causation_id`
- `execution_id`
- `event_type`
- `schema_version`
- `source_department`
- `destination_department`
- `priority`
- `risk_level`
- `payload`
- `created_at`

`correlation_id` connects the complete business operation.

`causation_id` identifies the event or action that caused the new event.

`schema_version` allows event formats to evolve safely.

## Shared Business Domains

All departments will use shared business domains as the single source of truth.

Shared domains include:

- Workspaces
- Users and memberships
- Products and prices
- Customers and leads
- Conversations
- Campaigns
- Orders
- Inventory
- Payments
- Deliveries
- Approvals
- Analytics
- Documents
- Knowledge bases
- Audit logs
- AI usage records

Departments must not maintain conflicting copies of shared business data.

## Reasoning and Execution Separation

LLMs may propose actions, but deterministic application services must validate and execute them.

```text
Agent proposes action
        ↓
Structured output
        ↓
Policy and permission validation
        ↓
Human approval when required
        ↓
Application service
        ↓
Database or external integration
```

Agents must never directly perform critical operations such as:

- Changing product prices
- Applying unrestricted discounts
- Reserving stock
- Processing payments
- Issuing refunds
- Changing order status

## AI Cost Control and Model Routing

AI cost control is a core architectural requirement.

The system must use the least expensive reliable execution path.

### Capability Tiers

```text
deterministic
economy
standard
premium
embedding
```

Agents and workflows request a capability tier. They must not select hard-coded provider model names.

Provider adapters map capability tiers to configured providers and models.

### No LLM

Normal application code should handle:

- Workspace validation
- Authentication and permissions
- Database operations
- Usage-limit checks
- Price-limit validation
- Order-state transitions
- Duplicate-event detection
- Approval-policy checks
- Known routing rules

### Economy Model

Economy models should handle:

- Intent classification
- Sales-stage detection
- Sentiment classification
- Simple extraction
- Conversation summaries
- Routing assistance
- Structured transformations

### Standard Model

Standard models should handle:

- Normal customer conversations
- Product explanations
- Objection handling
- Personalized follow-ups
- Routine qualification
- Standard sales reasoning

### Premium Model

Premium models should be reserved for:

- Difficult negotiations
- High-value opportunities
- Complex reasoning
- Sensitive customer situations
- Low-confidence escalations
- Tasks allowed by workspace policy

## Agent Activation Rules

For a normal customer message, the preferred flow is:

```text
Deterministic checks
        ↓
Optional economy classification
        ↓
One primary Sales Agent
        ↓
Optional specialist escalation
```

Specialist agents should activate only when:

- The primary agent lacks the required capability
- Complexity exceeds a configured threshold
- Confidence is below the accepted threshold
- Business policy requires specialist review
- The opportunity is classified as high value

Multiple agents must not independently analyze the same message without a justified reason.

## AI Usage Tracking

Every AI call must produce a usage record containing:

- Workspace identifier
- Department identifier
- Agent identifier
- Conversation identifier when available
- Customer or lead identifier when available
- Task identifier
- Workflow execution identifier
- Provider
- Model
- Capability tier
- Input tokens
- Output tokens
- Total tokens
- Context size
- Latency
- Estimated cost
- Routing reason
- Premium-model indicator
- Success or failure status
- Timestamp

## Workspace Limits

The platform must support workspace limits for:

- Conversations per billing period
- AI-generated messages
- Input tokens
- Output tokens
- Total tokens
- Total AI budget
- Premium-model calls
- Maximum context size
- AI calls per customer message
- AI calls per workflow
- Concurrent AI executions

Limits should be validated before calling an AI provider.

The platform must never silently exceed a workspace AI budget.

## Cost Reduction

The platform should reduce cost through:

- Conversation summaries
- Selective history loading
- Selective RAG retrieval
- Compact prompts
- Structured outputs
- Prompt reuse
- Retrieval caching
- Safe response caching
- Deterministic preprocessing
- Context limits
- Duplicate-request prevention
- Reusing previous classifications
- Specialist escalation only when necessary

The full conversation history must not be sent to an LLM by default.

## Limit Behavior

When a workspace reaches a configured limit, the platform must use an explicit policy:

- Fall back to a cheaper model
- Use deterministic handling
- Request human intervention
- Queue the task
- Reject the action with a clear usage-limit response

## Billing Preparation

The MVP does not require the complete billing system yet.

However, usage records and limits must support future:

- Subscription plans
- Included usage
- Overage billing
- Premium-model add-ons
- Workspace budgets
- Usage dashboards
- Cost and profit-margin reporting

## Durable Execution

Long-running operations must eventually survive server restarts and temporary failures.

Examples:

- Follow-ups
- Campaigns
- Payment confirmation
- Delivery tracking
- Research tasks
- Cross-department operations

The architecture must support:

- Workflow checkpoints
- Retry policies
- Idempotency
- Outbox processing
- Resume after failure
- Dead-letter handling
- Execution history

The MVP may begin with local execution, but business-domain code must not prevent future worker or durable-workflow integration.

## Initial Implementation

The current project represents the first implementation of the Sales Department.

Existing agents will gradually move under:

```text
departments/
└── sales/
    ├── supervisor/
    ├── agents/
    ├── workflows/
    └── services/
```

Marketing and Back-Office departments will be introduced after the shared platform and Sales MVP are stable.

The project will remain a modular monolith initially. Services will only be extracted when independent scaling, security, reliability, or deployment requirements justify it.

## Consequences

### Positive

- Clear responsibility boundaries
- Stronger security and auditability
- Controlled agent autonomy
- Lower and configurable AI costs
- Provider independence
- Better testing
- Reusable shared domains
- Safe future expansion
- Avoids one large supervisor prompt
- Avoids unnecessary agent calls

### Negative

- More typed contracts are required
- Execution tracking adds complexity
- Model routing and usage tracking require shared services
- Cross-department workflows require careful coordination
- Migration from the current structure must be gradual

## Final Architecture Rules

The platform must preserve this hierarchy:

```text
Business Supervisor
├── Sales Department Supervisor
├── Marketing Department Supervisor
└── Back-Office Department Supervisor
```

The Business Supervisor coordinates cross-department operations.

Department supervisors control their specialist agents and workflows.

Simple departmental tasks should bypass the Business Supervisor.

Every AI execution must pass through configurable model-routing, budget, quota, permission, and audit controls.

LLMs propose structured actions. Deterministic application services validate and execute them.