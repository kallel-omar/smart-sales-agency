import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderApp } from "./test/renderApp";
import { analyticsFixture, fixtures, installFetchMock, mockJson } from "./test/mockApi";
import type { ConversationMessageRead, DirectSalesReply, LeadRead } from "./types/api";

const leadFixtures: Record<string, LeadRead[]> = {
  "workspace-a": [
    {
      id: "lead-1",
      tenant_id: "workspace-1",
      full_name: "Casey Contact",
      company_name: "Northwind QA",
      job_title: "Operations lead",
      email: "casey@example.test",
      phone: null,
      website: null,
      source: "whatsapp_cloud",
      notes: null,
      status: "new",
      sales_stage: "discovery",
      score: 76,
      created_at: "2026-08-12T08:00:00Z",
      updated_at: "2026-08-12T08:10:00Z"
    },
    {
      id: "lead-2",
      tenant_id: "workspace-1",
      full_name: "Riley Retail",
      company_name: "Bright Market",
      job_title: null,
      email: null,
      phone: null,
      website: null,
      source: "console",
      notes: null,
      status: "qualified",
      sales_stage: "proposal",
      score: 84,
      created_at: "2026-08-12T07:00:00Z",
      updated_at: "2026-08-12T08:05:00Z"
    }
  ],
  "workspace-b": [
    {
      id: "lead-3",
      tenant_id: "workspace-2",
      full_name: "Jordan Workspace",
      company_name: "Other Tenant Labs",
      job_title: null,
      email: null,
      phone: null,
      website: null,
      source: "console",
      notes: null,
      status: "new",
      sales_stage: "qualification",
      score: 40,
      created_at: "2026-08-12T06:00:00Z",
      updated_at: "2026-08-12T06:05:00Z"
    }
  ]
};

const messageFixtures: Record<string, ConversationMessageRead[]> = {
  "lead-1": [
    {
      id: "message-1",
      lead_id: "lead-1",
      direction: "inbound",
      channel: "whatsapp_cloud",
      stage: "discovery",
      content: "I want to compare plans.",
      created_at: "2026-08-12T08:01:00Z"
    },
    {
      id: "message-2",
      lead_id: "lead-1",
      direction: "outbound",
      channel: "whatsapp_cloud",
      stage: "discovery",
      content: "Happy to help compare them.",
      created_at: "2026-08-12T08:02:00Z"
    }
  ],
  "lead-2": [],
  "lead-3": [
    {
      id: "message-3",
      lead_id: "lead-3",
      direction: "inbound",
      channel: "console",
      stage: "qualification",
      content: "This belongs to workspace B.",
      created_at: "2026-08-12T06:02:00Z"
    }
  ]
};

function installDefaultApi() {
  return installFetchMock((url, init) => {
    if (url.endsWith("/api/auth/login")) {
      return mockJson(fixtures.token);
    }
    if (url.endsWith("/api/auth/me")) {
      const headers = init?.headers as Headers;
      return headers.get("Authorization") ? mockJson(fixtures.user) : mockJson({ detail: "Unauthorized" }, 401);
    }
    if (url.endsWith("/api/workspaces")) {
      return mockJson(fixtures.workspaces);
    }
    if (url.endsWith("/api/leads")) {
      return mockJson([]);
    }
    if (url.endsWith("/api/approvals")) {
      return mockJson([]);
    }
    if (url.includes("/api/operator/workforce")) {
      return mockJson([]);
    }
    if (url.includes("/api/operator/work-items")) {
      return mockJson([]);
    }
    if (url.includes("/api/operator/analytics")) {
      return mockJson(analyticsFixture);
    }
    if (url.endsWith("/api/integrations/accounts")) {
      return mockJson([]);
    }
    if (url.endsWith("/api/integrations/operational-summary")) {
      return mockJson({
        active_integration_account_count: 0,
        pending_outbound_action_count: 0,
        delivered_outbound_action_count: 0,
        failed_outbound_action_count: 0,
        retryable_failed_action_count: 0,
        cancelled_outbound_action_count: 0,
        expired_outbound_action_count: 0,
        most_recent_outbound_at: null,
        recent_delivered_count: 0,
        recent_failed_count: 0,
        priority_counts: {},
        owned_outbound_action_count: 0,
        unowned_outbound_action_count: 0,
        archived_outbound_action_count: 0,
        unarchived_outbound_action_count: 0
      });
    }
    if (url.endsWith("/api/integrations/ai-usage/summary")) {
      return mockJson({
        invocation_count: 0,
        successful_invocation_count: 0,
        failed_invocation_count: 0,
        input_tokens: 0,
        output_tokens: 0,
        total_tokens: 0,
        unknown_token_usage_invocation_count: 0,
        known_estimated_spend: "0",
        unknown_pricing_invocation_count: 0
      });
    }
    return mockJson({ detail: "Unhandled test route" }, 500);
  });
}

function selectedWorkspace(init?: RequestInit) {
  return ((init?.headers as Headers | undefined)?.get("X-Workspace-Slug") ?? "workspace-a");
}

function installInboxApi(
  overrides: {
    reply?: (url: string, init?: RequestInit) => Response | Promise<Response>;
    conversation?: (leadId: string, init?: RequestInit) => Response | Promise<Response>;
  } = {}
) {
  return installFetchMock((url, init) => {
    if (url.endsWith("/api/auth/me")) {
      return mockJson(fixtures.user);
    }
    if (url.endsWith("/api/workspaces")) {
      return mockJson(fixtures.workspaces);
    }
    if (url.endsWith("/api/leads")) {
      return mockJson(leadFixtures[selectedWorkspace(init)] ?? []);
    }
    const conversationMatch = url.match(/\/api\/conversations\/([^/?]+)(?:\?limit=\d+)?$/);
    if (conversationMatch) {
      return overrides.conversation?.(conversationMatch[1], init) ?? mockJson(messageFixtures[conversationMatch[1]] ?? []);
    }
    const replyMatch = url.match(/\/api\/conversations\/([^/?]+)\/reply$/);
    if (replyMatch) {
      return (
        overrides.reply?.(url, init) ??
        mockJson({
          lead_id: replyMatch[1],
          detected_stage: "proposal",
          draft_reply: "Here is a draft from the backend Sales engine.",
          approval_id: "approval-1",
          handoff_required: false,
          handoff_reason_code: null,
          duplicate: false
        } satisfies DirectSalesReply)
      );
    }
    if (url.endsWith("/api/approvals")) {
      return mockJson([]);
    }
    return mockJson({ detail: "Unhandled test route" }, 500);
  });
}

describe("HIRI frontend foundation", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the login form", () => {
    installDefaultApi();
    renderApp("/login");

    expect(screen.getByRole("heading", { name: /sign in to your workspace/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/email/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument();
  });

  it("transitions from successful auth to the app shell", async () => {
    const user = userEvent.setup();
    installDefaultApi();
    renderApp("/login");

    await user.type(screen.getByLabelText(/email/i), "operator@example.test");
    await user.type(screen.getByLabelText(/password/i), "correct-password");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    expect(await screen.findByText("Smart Sales Agency")).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: /dashboard/i })).toBeInTheDocument();
  });

  it("shows invalid credentials for 401 login failures", async () => {
    const user = userEvent.setup();
    installFetchMock((url) => {
      if (url.endsWith("/api/auth/login")) {
        return mockJson({ detail: "Invalid credentials" }, 401);
      }
      return mockJson({ detail: "Unhandled test route" }, 500);
    });
    renderApp("/login");

    await user.type(screen.getByLabelText(/email/i), "operator@example.test");
    await user.type(screen.getByLabelText(/password/i), "wrong-password");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid credentials");
  });

  it("shows a generic message for non-401 login API failures", async () => {
    const user = userEvent.setup();
    installFetchMock((url) => {
      if (url.endsWith("/api/auth/login")) {
        return mockJson({ detail: "Backend unavailable" }, 503);
      }
      return mockJson({ detail: "Unhandled test route" }, 500);
    });
    renderApp("/login");

    await user.type(screen.getByLabelText(/email/i), "operator@example.test");
    await user.type(screen.getByLabelText(/password/i), "correct-password");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Unable to sign in");
    expect(screen.queryByText("Invalid credentials")).not.toBeInTheDocument();
  });

  it("redirects protected routes without authentication", async () => {
    installDefaultApi();
    renderApp("/app/leads");

    expect(await screen.findByRole("heading", { name: /sign in to your workspace/i })).toBeInTheDocument();
  });

  it("renders sidebar navigation for authenticated users", async () => {
    localStorage.setItem("hiri.auth.accessToken", "test-access-token");
    installDefaultApi();
    renderApp("/app");

    const navigation = await screen.findByRole("navigation", { name: /primary navigation/i });
    expect(within(navigation).getByRole("link", { name: /inbox/i })).toBeInTheDocument();
    expect(within(navigation).getByRole("link", { name: /approvals/i })).toBeInTheDocument();
    expect(within(navigation).getByRole("link", { name: /workforce/i })).toBeInTheDocument();
    expect(within(navigation).getByRole("link", { name: /workitems/i })).toBeInTheDocument();
  });

  it("loads and switches workspace selection", async () => {
    const user = userEvent.setup();
    localStorage.setItem("hiri.auth.accessToken", "test-access-token");
    installDefaultApi();
    renderApp("/app");

    const selector = await screen.findByRole("combobox", { name: /workspace/i });
    expect(selector).toHaveValue("workspace-a");

    await user.selectOptions(selector, "workspace-b");

    expect(selector).toHaveValue("workspace-b");
    expect(localStorage.getItem("hiri.workspace.slug")).toBe("workspace-b");
  });

  it("renders dashboard empty states safely", async () => {
    localStorage.setItem("hiri.auth.accessToken", "test-access-token");
    installDefaultApi();
    renderApp("/app");

    expect(await screen.findByText("No leads yet")).toBeInTheDocument();
    expect(screen.getByText("Workspace readiness")).toBeInTheDocument();
  });

  it("renders dashboard error states safely", async () => {
    localStorage.setItem("hiri.auth.accessToken", "test-access-token");
    installFetchMock((url) => {
      if (url.endsWith("/api/auth/me")) {
        return mockJson(fixtures.user);
      }
      if (url.endsWith("/api/workspaces")) {
        return mockJson(fixtures.workspaces);
      }
      if (url.endsWith("/api/leads")) {
        return mockJson({ detail: "database secret should not render" }, 500);
      }
      if (url.includes("/api/operator/analytics")) {
        return mockJson(analyticsFixture);
      }
      return mockJson([]);
    });
    renderApp("/app");

    expect(await screen.findByRole("alert", {}, { timeout: 3000 })).toHaveTextContent("could not be loaded");
    expect(screen.queryByText("database secret should not render")).not.toBeInTheDocument();
  });

  it("renders the Inbox with workspace leads and selected conversation history", async () => {
    localStorage.setItem("hiri.auth.accessToken", "test-access-token");
    installInboxApi();
    renderApp("/app/inbox");

    expect(await screen.findByRole("heading", { name: /conversations/i })).toBeInTheDocument();
    expect(await screen.findAllByText("Casey Contact")).not.toHaveLength(0);
    expect(screen.getAllByText("Northwind QA")).not.toHaveLength(0);
    expect(await screen.findByText("I want to compare plans.")).toBeInTheDocument();
    expect(screen.getByText("Happy to help compare them.")).toBeInTheDocument();
  });

  it("loads the selected lead thread and preserves empty conversation state", async () => {
    const user = userEvent.setup();
    localStorage.setItem("hiri.auth.accessToken", "test-access-token");
    const fetchMock = installInboxApi();
    renderApp("/app/inbox");

    await user.click(await screen.findByRole("button", { name: /Riley Retail/i }));

    expect(await screen.findByText("No messages yet")).toBeInTheDocument();
    const historyRequest = fetchMock.mock.calls.find(([url]) =>
      String(url).endsWith("/api/conversations/lead-2?limit=100")
    );
    expect(historyRequest).toBeDefined();
    const headers = historyRequest?.[1]?.headers as Headers;
    expect(headers.get("X-Workspace-Slug")).toBe("workspace-a");
  });

  it("submits replies through the backend conversation route and refreshes state", async () => {
    const user = userEvent.setup();
    localStorage.setItem("hiri.auth.accessToken", "test-access-token");
    let resolveReply: (response: Response) => void = () => undefined;
    const fetchMock = installInboxApi({
      reply: () =>
        new Promise<Response>((resolve) => {
          resolveReply = resolve;
        })
    });
    renderApp("/app/inbox");

    await screen.findByText("I want to compare plans.");
    await user.type(screen.getByLabelText(/reply to customer/i), "Please prepare a proposal.");
    await user.click(screen.getByRole("button", { name: /send to sales/i }));

    expect(screen.getByRole("button", { name: /sending/i })).toBeDisabled();
    resolveReply(
      mockJson({
        lead_id: "lead-1",
        detected_stage: "proposal",
        draft_reply: "Backend draft waiting for approval.",
        approval_id: "approval-2",
        handoff_required: false,
        handoff_reason_code: null,
        duplicate: false
      } satisfies DirectSalesReply)
    );

    expect(await screen.findByText("Approval required")).toBeInTheDocument();
    expect(screen.getByText("Backend draft waiting for approval.")).toBeInTheDocument();
    expect(screen.getByLabelText(/reply to customer/i)).toHaveValue("");

    const replyRequest = await waitFor(() => {
      const request = fetchMock.mock.calls.find(([url]) =>
        String(url).endsWith("/api/conversations/lead-1/reply")
      );
      if (!request) {
        throw new Error("Reply request was not sent");
      }
      return request;
    });
    const replyHeaders = replyRequest[1]?.headers as Headers;
    expect(replyHeaders.get("X-Workspace-Slug")).toBe("workspace-a");
    expect(replyHeaders.get("Idempotency-Key")).toMatch(/\S+/);
    expect(JSON.parse(replyRequest[1]?.body as string)).toEqual({
      channel: "whatsapp_cloud",
      content: "Please prepare a proposal."
    });
  });

  it("shows safe reply failures without exposing backend detail", async () => {
    const user = userEvent.setup();
    localStorage.setItem("hiri.auth.accessToken", "test-access-token");
    installInboxApi({
      reply: () => mockJson({ detail: "raw backend failure detail" }, 500)
    });
    renderApp("/app/inbox");

    await screen.findByText("I want to compare plans.");
    await user.type(screen.getByLabelText(/reply to customer/i), "Can the backend answer?");
    await user.click(screen.getByRole("button", { name: /send to sales/i }));

    expect(await screen.findByText("Unable to send this message to the backend Sales engine.")).toBeInTheDocument();
    expect(screen.queryByText(/raw backend failure detail/i)).not.toBeInTheDocument();
  });

  it("refreshes Inbox data when the selected workspace changes", async () => {
    const user = userEvent.setup();
    localStorage.setItem("hiri.auth.accessToken", "test-access-token");
    const fetchMock = installInboxApi();
    renderApp("/app/inbox");

    expect(await screen.findByText("Casey Contact")).toBeInTheDocument();
    await user.selectOptions(await screen.findByRole("combobox", { name: /workspace/i }), "workspace-b");

    expect(await screen.findAllByText("Jordan Workspace")).not.toHaveLength(0);
    expect(screen.queryByText("Casey Contact")).not.toBeInTheDocument();
    expect(await screen.findByText("This belongs to workspace B.")).toBeInTheDocument();

    const workspaceBLeadsRequest = fetchMock.mock.calls.find(([url, init]) => {
      const headers = init?.headers as Headers | undefined;
      return String(url).endsWith("/api/leads") && headers?.get("X-Workspace-Slug") === "workspace-b";
    });
    expect(workspaceBLeadsRequest).toBeDefined();
  });
});
