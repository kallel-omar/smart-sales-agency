Smart Sales Agency

A multi-agent AI sales platform for lead qualification, sales conversations, follow-ups, approvals, and workspace-scoped business operations.

Status: 🚧 Active Development — backend architecture and Sales Department foundation are being built and tested. The dashboard concepts shown in the portfolio represent the product direction and are not yet a finished production UI.



Portfolio: kallel-omar.github.io · GitHub: @kallel-omar · LinkedIn: kallelomar

Overview

Smart Sales Agency is an evolving AI-powered sales SaaS designed around a hierarchical, event-driven multi-agent architecture.

The goal is not to put an LLM behind every operation. The platform separates deterministic business logic from AI-assisted work so routing, permissions, approvals, workspace isolation, and future cost controls can remain predictable and testable.

The current codebase establishes the backend foundation of the Sales Department: leads can be researched and qualified, inbound conversations can be processed by specialist agents, outreach can be drafted, and sensitive outbound actions can be held for human approval.

The long-term product direction expands this foundation into coordinated Sales, Marketing, and Back-Office departments operating on shared business data.

What the Current Version Implements

Multi-agent Sales foundation

Sales Department Supervisor — deterministic routing for known sales events.

Lead Research Agent — creates a structured research brief from supplied lead information.

Qualification Agent — evaluates and scores leads using visible qualification logic.

Sales Conversation Agent — detects the sales stage and drafts a contextual reply.

Follow-up Agent — foundation for follow-up operations.

LangGraph new-lead workflow — orchestrates lead loading, research, qualification, conditional routing, and outreach preparation.

Human-in-the-loop sales operations

Outbound actions can require explicit human approval before execution.

The current flow supports:

Draft → Pending Approval → Approve / Reject → Execute

For safe local development, approved demo messages are delivered through a console channel rather than being sent to a real customer.

Workspace-aware API foundation

The project includes workspace creation and lookup plus workspace-scoped access for core sales data.

Current workspace protections cover operations such as:

leads

products

conversation history

workflow execution

Workspace isolation is being developed and tested as a core architectural requirement rather than added later as a SaaS afterthought.

Business-event foundation

The codebase introduces typed business events and an internal event dispatcher.

Currently registered core events include:

lead.generated

lead.qualified

The in-memory dispatcher is intentionally replaceable so the system can later move to an outbox, queue, or message broker without changing the business-event contracts.

Provider-independent LLM boundary

The platform can operate in:

demo mode — no paid AI API required

openai_compatible mode — compatible with OpenAI-style providers such as OpenAI, Groq, OpenRouter, or compatible local servers

This keeps agent code separated from a single model vendor.

Architecture

flowchart TD
    E[Business Event / API Request] --> SD[Sales Department Service]
    SD --> SS[Sales Department Supervisor]

    SS -->|New Lead| NW[New Lead Workflow]
    SS -->|Inbound Message| SC[Sales Conversation Agent]
    SS -->|Follow-up Due| FU[Follow-up Agent]

    NW --> LR[Lead Research Agent]
    LR --> QA[Qualification Agent]
    QA -->|Qualified| OD[Prepare Outreach]
    QA -->|Unqualified| STOP[Stop / Collect More Information]

    SC --> DR[Draft Reply]
    OD --> HA[Human Approval]
    DR --> HA

    HA -->|Approved| CH[Channel Adapter]
    HA -->|Rejected| RJ[No Delivery]

    DB[(SQL Database)] --- SD
    LLM[LLM Provider Boundary] --- LR
    LLM --- SC

Design principle

Known routing rules are deliberately deterministic.

Known business rule  → deterministic code
Language/reasoning    → AI agent
Sensitive action      → human approval
External delivery     → channel adapter

This approach is intended to make the platform easier to audit, test, control, and eventually optimize for AI cost.

Current New-Lead Workflow

flowchart LR
    A[Load Lead] --> B[Research]
    B --> C[Qualify]
    C -->|Qualified| D[Prepare Outreach]
    D --> E[Human Approval]
    C -->|Unqualified| F[Stop / Archive / Gather More Data]

A qualified lead produces an outreach draft and approval request. An unqualified lead stops before outbound contact.

Technology Stack

Area

Technology

Language

Python 3.11+

API

FastAPI

Agent orchestration

LangGraph

Data models / ORM

SQLModel

Validation / settings

Pydantic Settings

Local database

SQLite

Production-ready database option

PostgreSQL + Psycopg

HTTP client

HTTPX

Testing

Pytest + pytest-asyncio

Linting

Ruff

Packaging

Hatchling

Containers

Docker / Docker Compose

API Surface

The current FastAPI application exposes routes for:

Resource

Purpose

/api/workspaces

Create and inspect workspaces

/api/leads

Create, list, and retrieve workspace leads

/api/products

Create and list workspace products

/api/workflows

Execute sales workflows

/api/conversations

Retrieve history and process inbound sales messages

/api/approvals

List, approve, and reject pending actions

/api/integrations/inbound-events

Receive provider-neutral inbound events through a replaceable integration boundary

`X-Integration-Key` is resolved server-side against an active, persisted
integration account that belongs to one workspace. Only a one-way credential
hash is stored; the event body never establishes a workspace.
The inbound route also authenticates the webhook before Sales processing.
The included generic HMAC adapter expects `X-Webhook-Signature` and
`X-Webhook-Timestamp`. Each integration account holds an internal
`secret_reference`, such as `WEBHOOK_GENERIC_HMAC_SECRET`; the environment
secret resolver reads that variable only at verification time. Secret values and
references are never returned in account responses. Provider adapters remain
outside the core domain.

/health

Service health and active LLM mode

Interactive OpenAPI documentation is available locally at:

http://127.0.0.1:8000/docs

Data Model

The current persistence layer includes:

Workspace

Lead

Product

LeadResearch

ConversationMessage

ApprovalRequest

FollowUpTask

Lead state and sales conversations use explicit enums for lead status, sales stage, and approval status.

Project Structure

smart-sales-agency/
├── app/
│   ├── agents/                    # Compatibility imports / earlier agent paths
│   ├── api/
│   │   └── routes/                # FastAPI endpoints
│   ├── channels/                  # Delivery adapter boundary
│   ├── core/                      # Business events and dispatcher
│   ├── departments/
│   │   └── sales/
│   │       ├── agents/            # Sales specialist agents
│   │       ├── services/          # Department application boundary
│   │       ├── supervisor/        # Deterministic department routing
│   │       └── workflows/         # LangGraph workflows
│   ├── services/                  # LLM, repositories, workspaces
│   ├── config.py
│   ├── db.py
│   ├── main.py
│   ├── models.py
│   └── schemas.py
├── docs/
│   ├── ARCHITECTURE.md
│   └── OPEN_SOURCE_RESEARCH.md
├── examples/
├── tests/
├── .env.example
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
└── requirements.txt

Running Locally

1. Clone the repository

git clone https://github.com/kallel-omar/smart-sales-agency.git
cd smart-sales-agency

2. Create a virtual environment

Windows PowerShell

python -m venv .venv
.\.venv\Scripts\Activate.ps1

Linux / macOS

python -m venv .venv
source .venv/bin/activate

3. Install dependencies

pip install -e ".[dev]"

4. Configure the environment

Copy the example configuration:

Windows

Copy-Item .env.example .env

Linux / macOS

cp .env.example .env

The default configuration uses SQLite and offline demo AI mode, so no paid API is required.

5. Start the API

uvicorn app.main:app --reload

Then open:

API documentation: http://127.0.0.1:8000/docs

Health endpoint: http://127.0.0.1:8000/health

LLM Configuration

Offline demo mode

LLM_MODE=demo

This is the safest way to explore the project without API costs.

OpenAI-compatible provider

LLM_MODE=openai_compatible
LLM_API_KEY=your_api_key
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=your_supported_model
LLM_TIMEOUT_SECONDS=45

The same boundary can be configured for compatible providers by changing the base URL and model.

Never commit your .env file or API keys.

PostgreSQL with Docker

Set the database URL:

DATABASE_URL=postgresql+psycopg://sales_agency:local_password@postgres:5432/sales_agency

Then run:

docker compose up --build

Example Workflow

Before creating leads or products, create an active workspace through the API.

After that, a typical sales flow is:

Create Workspace
      ↓
Create Product
      ↓
Create Lead
      ↓
Run New-Lead Workflow
      ↓
Research + Qualification
      ↓
Qualified?
   ↙       ↘
 No         Yes
 ↓           ↓
Stop      Draft Outreach
              ↓
        Human Approval
              ↓
       Execute Safe Delivery

The included examples/ directory contains sample lead and product payloads.

Testing

The repository contains Pytest coverage for core architectural behavior, including:

workspace scoping

workspace data isolation

workspace operation isolation

typed event payloads

business-event contracts

event-type registration

internal event dispatching

Sales Department service behavior

supervisor routing

agents

approvals

workspaces

Run the test suite with:

pytest

Run linting with:

ruff check .

Safety & Control Principles

Smart Sales Agency is intentionally being built with bounded automation.

Current design rules include:

deterministic routing where AI is unnecessary

human approval before outbound delivery by default

no automatic invention of product price or stock

explicit workspace boundaries

provider-independent AI integration

typed business-event contracts

replaceable channel adapters

replaceable internal event transport

These constraints are important for turning an AI demo into a controllable business platform.

Product Direction

The current repository is not presented as a finished autonomous sales platform. It is the evolving backend foundation.

In progress / planned

Complete hierarchical Business Supervisor architecture

Marketing Department

Back-Office Department

richer cross-department business events

authentication and production-grade authorization

complete tenant/workspace enforcement across all entities and operations

production CRM/dashboard frontend

official WhatsApp Cloud API integration and webhook handling

additional communication channels

persistent LangGraph checkpoints

background processing for scheduled follow-ups

product/policy knowledge retrieval and RAG

granular human-approval policies

audit logging

AI usage and cost metering

configurable model routing

workspace quotas and limits

rate limiting

observability and evaluation

production deployment infrastructure

Long-term architecture

Business Supervisor
├── Sales Department Supervisor
│   ├── Lead Research
│   ├── Qualification
│   ├── Sales Conversation
│   └── Follow-up
│
├── Marketing Department Supervisor
│   └── Planned
│
└── Back-Office Department Supervisor
    └── Planned

The architecture is intended to scale by adding bounded departments and specialist agents rather than creating an uncontrolled flat network of agents.

Development Philosophy

The project follows a few core principles:

Do not use an LLM for deterministic work.

Keep business rules outside model prompts whenever possible.

Require human approval for sensitive actions.

Keep AI providers replaceable.

Treat workspace isolation as an architectural requirement.

Use typed events to reduce coupling between departments.

Build observable workflows before increasing autonomy.

Optimize AI cost through routing rather than using premium models everywhere.

Documentation

Additional technical notes are available in:

docs/ARCHITECTURE.md

docs/OPEN_SOURCE_RESEARCH.md

Author

Omar Kallel

Full-Stack Developer · AI & SaaS Developer

Portfolio: https://kallel-omar.github.io

LinkedIn: linkedin.com/in/kallelomar

GitHub: github.com/kallel-omar

License

This project is licensed under the MIT License.

Smart Sales Agency is under active development. Features described as planned or in progress are part of the product roadmap and should not be interpreted as currently production-ready functionality.
