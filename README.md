# Smart Sales Agency Starter

A clean MVP for a **multi-agent AI sales agency**, designed to be understandable before becoming fully autonomous.

## Included

- Supervisor Agent: routes new leads, inbound messages, and follow-up events.
- Lead Research Agent: creates an evidence-aware brief from supplied data.
- Qualification Agent: scores leads using visible rules.
- Sales Conversation Agent: detects sales stage and drafts a reply.
- Follow-up Agent: schedules future tasks.
- LangGraph workflow: research -> qualification -> outreach draft.
- FastAPI REST API.
- SQLite by default; PostgreSQL-ready through `DATABASE_URL`.
- Product catalog, conversation history, approvals, and follow-up data models.
- Human approval before outbound delivery.
- Offline demo mode requiring no paid AI API.
- OpenAI-compatible provider boundary for OpenAI, Groq, OpenRouter, or compatible local servers.
- Safe console channel and disabled WhatsApp stub.

## Run locally

```bash
python -m venv .venv
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
# Linux/macOS
# source .venv/bin/activate

pip install -e ".[dev]"
copy .env.example .env   # Windows
# cp .env.example .env  # Linux/macOS
uvicorn app.main:app --reload
```

Open:

- API docs: `http://127.0.0.1:8000/docs`
- Health: `http://127.0.0.1:8000/health`

## First test

### 1. Add a product

```bash
curl -X POST http://127.0.0.1:8000/api/products \
  -H "Content-Type: application/json" \
  --data @examples/product.json
```

### 2. Add a lead

```bash
curl -X POST http://127.0.0.1:8000/api/leads \
  -H "Content-Type: application/json" \
  --data @examples/lead.json
```

Copy the returned lead `id`.

### 3. Run the agent workflow

```bash
curl -X POST http://127.0.0.1:8000/api/workflows/LEAD_ID/run
```

The result contains a draft and an `approval_id` when the lead is qualified.

### 4. Approve the demo delivery

```bash
curl -X POST http://127.0.0.1:8000/api/approvals/APPROVAL_ID/approve \
  -H "Content-Type: application/json" \
  -d '{"reviewer_note":"Approved for demo"}'
```

The message is printed to the terminal instead of being sent externally.

## Use Groq or another OpenAI-compatible provider

Set these values in `.env`:

```env
LLM_MODE=openai_compatible
LLM_API_KEY=your_key
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_MODEL=your_supported_model
```

Do not commit `.env`.

## PostgreSQL with Docker

Change `.env`:

```env
DATABASE_URL=postgresql+psycopg://sales_agency:local_password@postgres:5432/sales_agency
```

The PostgreSQL driver is already included. Then run:

```bash
docker compose up --build
```

## Recommended next build order

1. Add authentication and tenant/workspace isolation.
2. Add a proper CRM dashboard in Next.js.
3. Add knowledge-base retrieval for products and policies.
4. Add persistent LangGraph checkpoints and approval interrupts.
5. Implement the official WhatsApp Cloud API adapter and webhook.
6. Add background jobs for due follow-ups.
7. Add audit logs, rate limits, usage metering, and evaluation tests.

Read `docs/ARCHITECTURE.md` and `docs/OPEN_SOURCE_RESEARCH.md` before extending it.
