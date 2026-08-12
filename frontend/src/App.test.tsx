import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderApp } from "./test/renderApp";
import { fixtures, installFetchMock, mockJson } from "./test/mockApi";

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
    expect(await screen.findByRole("link", { name: /overview/i })).toBeInTheDocument();
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
    expect(within(navigation).getByRole("link", { name: /ai sales team/i })).toBeInTheDocument();
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
      return mockJson([]);
    });
    renderApp("/app");

    expect(await screen.findByRole("alert", {}, { timeout: 3000 })).toHaveTextContent("could not be loaded");
    expect(screen.queryByText("database secret should not render")).not.toBeInTheDocument();
  });
});
