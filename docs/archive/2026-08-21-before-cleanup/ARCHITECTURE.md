# Architecture

```text
Inbound event
    |
    v
Supervisor Agent (deterministic router)
    |
    +--> New lead: Lead Researcher -> Qualifier -> Outreach Draft -> Human Approval
    |
    +--> Customer message: Sales Conversation Agent -> Human Approval
    |
    +--> Follow-up due: Follow-up Agent

Shared services
- SQL database: leads, products, research, conversations, approvals, follow-ups
- LLM provider abstraction: offline demo or OpenAI-compatible API
- Channel adapters: console demo; WhatsApp safe stub
```

## Why this MVP is intentionally bounded

- It does not scrape websites automatically.
- It does not send messages without approval by default.
- It does not invent prices or stock.
- The supervisor uses deterministic routing before introducing LLM autonomy.
- Every future external tool should be tenant-scoped, permissioned, logged, and rate-limited.
