# WhatsApp Cloud Channel Foundation

Task 286 introduces `whatsapp_cloud` as the first concrete customer-channel
transport. It represents Meta's official WhatsApp Cloud API only, not Twilio,
WhatsApp Web session automation, or another WhatsApp transport.

## Official Contracts Inspected

- Meta WhatsApp Business Platform Cloud API / Postman API Network:
  `https://www.postman.com/meta/whatsapp-business-platform`
- WhatsApp Cloud webhook payload reference:
  `https://www.postman.com/meta/whatsapp-business-platform/folder/vzaxn16/webhook-payload-reference`
- WhatsApp received text message fixture contract:
  `https://www.postman.com/meta/whatsapp-business-platform/request/cy6hnq7/received-text-message`
- WhatsApp message status webhook contract:
  `https://www.postman.com/meta/whatsapp-business-platform/request/rgtfq23/message-status-update-notifications`
- Meta webhook verification and signed payload contract:
  `https://www.postman.com/meta/messenger-platform-api/folder/22794852-b5d97624-14d8-4e67-a2e4-529add49ca58`
- n8n WhatsApp Business Cloud node documentation:
  `https://docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-base.whatsapp`
- n8n WhatsApp Business Cloud credentials documentation:
  `https://docs.n8n.io/integrations/builtin/credentials/whatsapp/`
- n8n Webhook node raw-body behavior from the official `n8n-io/n8n` source:
  `https://github.com/n8n-io/n8n/blob/master/packages/nodes-base/nodes/Webhook/Webhook.node.ts`
- n8n Code node documentation for built-in Node.js `crypto` availability:
  `https://docs.n8n.io/code/code-node/`

## Architecture

WhatsApp/Meta sends webhooks to self-hosted n8n. n8n verifies Meta at the
provider edge, normalizes only supported text messages, and calls FastAPI with
the existing IntegrationAccount machine-auth headers. FastAPI remains the
authority for workspace resolution, lead scoping, idempotency, conversation
state, approvals, handoff, AI routing, cost tracking, and outbound action state.

WhatsApp-specific payloads stay out of Sales services and LangGraph. n8n has no
Sales prompts, no product lookup logic, no approval rules, and no workspace
selection logic.

## Provider Account Mapping

`IntegrationAccount.provider` is `whatsapp_cloud`.

`IntegrationAccount.external_account_id` stores the configured receiving
WhatsApp phone-number ID. Incoming webhook payload fields such as workspace ID
or workspace slug are never trusted. The account is first authenticated through
the persisted inbound credential and HMAC secret reference, then FastAPI checks
that the normalized recipient account matches the server-owned account record.

## Secrets

FastAPI stores only one-way inbound credential hashes and
`INTEGRATION_SECRET_*` references. Meta App Secret, webhook verify token,
access token, permanent token, and n8n encryption key stay in runtime
configuration or n8n credentials. FastAPI outbound action payloads reject
provider credential keys for `whatsapp_cloud`.

The n8n workflow uses placeholders:

- `WHATSAPP_CLOUD_VERIFY_TOKEN`
- `WHATSAPP_CLOUD_APP_SECRET`
- `WHATSAPP_CLOUD_PHONE_NUMBER_ID`
- `WHATSAPP_CLOUD_GRAPH_API_BASE_URL`
- `WHATSAPP_CLOUD_GRAPH_API_VERSION`
- `SSA_WHATSAPP_CLOUD_INTEGRATION_KEY`
- `SSA_WHATSAPP_CLOUD_HMAC_SECRET`

Task 286 commits no real Meta credentials and performs no live Meta requests.

## Verification And Authenticity

The verification path handles Meta's `hub.mode`, `hub.verify_token`, and
`hub.challenge` contract. It returns the challenge only when the mode is
`subscribe` and the configured verify token matches.

POST authenticity is checked at the n8n edge with `X-Hub-Signature-256`.
The workflow computes HMAC-SHA256 over the raw body using the Meta App Secret
and compares the `sha256=` value before normalization. n8n then signs the
normalized FastAPI request with the existing `X-Webhook-Timestamp` and
`X-Webhook-Signature` IntegrationAccount machine-auth contract.

## Inbound Text Normalization

The normalized FastAPI body is text-only:

```json
{
  "channel": "whatsapp_cloud",
  "provider_event_id": "wamid.fake",
  "sender_external_id": "15557654321",
  "recipient_account_id": "555666777888999",
  "content": "What is the monthly price?",
  "timestamp": 1720000000,
  "provider_metadata": {
    "waba_id": "111122223333444",
    "display_phone_number": "15551234567",
    "message_type": "text"
  }
}
```

FastAPI resolves the lead by the sender external ID within the authenticated
workspace. The WhatsApp sender is a customer channel identity, not a platform
`User` or `WorkspaceMember`.

Status-only webhooks are classified as provider noise. Image, audio, video,
document, location, interactive, and reaction messages are classified as
unsupported/deferred and are not converted into fake text.

## Idempotency

The WhatsApp message ID becomes the canonical external event ID. FastAPI's
existing Task 251 `InboundIntegrationEventReceipt` remains the sole durable
idempotency authority. n8n does not persist canonical deduplication state.

## Outbound Mapping

Task 286 prepares text-send transport mapping only. The persisted outbound
action remains canonical. n8n maps:

- `external_target_id` to WhatsApp `to`
- `content` to `text.body`
- configured phone-number ID to the `/messages` path
- configured Graph API base URL/version to the final URL

The Graph API version is a transport environment value, not hard-coded in Sales
or domain services. Live send, delivery receipt mapping, templates, and media
belong to later tasks.

## Task 287 Prerequisites

Before Task 287 can connect live traffic, provide a real Meta developer app, a
WhatsApp Business Account, a registered phone-number ID, a public HTTPS webhook
URL, the configured webhook verify token, the Meta App Secret, a send-capable
access credential in n8n, and a deliberate Graph API version value validated
against the current Meta docs.
