import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderApp } from "./test/renderApp";
import { analyticsFixture, fixtures, installFetchMock, mockJson } from "./test/mockApi";
import type { OperatorAnalyticsRead } from "./types/api";

function installAnalyticsApi(
  analytics: Response | Promise<Response> = mockJson(analyticsFixture)
) {
  return installFetchMock((url) => {
    if (url.endsWith("/api/auth/me")) return mockJson(fixtures.user);
    if (url.endsWith("/api/workspaces")) return mockJson(fixtures.workspaces);
    if (url.includes("/api/operator/analytics")) return analytics;
    return mockJson({ detail: "Unhandled test route" }, 500);
  });
}

describe("analytics page", () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem("hiri.auth.accessToken", "test-access-token");
  });

  afterEach(() => vi.unstubAllGlobals());

  it("shows the analytics loading state", async () => {
    installAnalyticsApi(new Promise(() => undefined));
    renderApp("/app/analytics");

    expect(await screen.findByText("Loading analytics")).toBeInTheDocument();
  });

  it("renders populated workforce, approval, AI, and Sales metrics", async () => {
    installAnalyticsApi();
    renderApp("/app/analytics");

    expect(await screen.findByText("Follow-up Specialist")).toBeInTheDocument();
    expect(screen.getByText("Approval request rate")).toBeInTheDocument();
    expect(screen.getByText("25.0%")).toBeInTheDocument();
    expect(screen.getByText("1,200")).toBeInTheDocument();
    expect(screen.getByText("Lead status breakdown")).toBeInTheDocument();
    expect(screen.getByText("Follow Up Lead")).toBeInTheDocument();
  });

  it("renders zero activity and null rates without NaN", async () => {
    const empty: OperatorAnalyticsRead = {
      ...analyticsFixture,
      workitems: { ...analyticsFixture.workitems, created: 0, completed: 0, failed: 0, success_rate: null, average_completion_seconds: null, by_work_type: [] },
      workforce: [],
      capabilities: [],
      approvals: { requests_created: 0, pending: 0, approved: 0, rejected: 0, workitems_with_approval_request: 0, approval_request_rate: null },
      ai_usage: { ...analyticsFixture.ai_usage, invocation_count: 0, input_tokens: 0, output_tokens: 0, total_tokens: 0, known_estimated_cost: "0", by_provider: [], by_model: [] },
      sales: { ...analyticsFixture.sales, total_leads: 0, leads_created: 0, won_leads: 0, by_status: { new: 0 } }
    };
    installAnalyticsApi(mockJson(empty));
    renderApp("/app/analytics");

    expect(await screen.findByText("No activity in this period")).toBeInTheDocument();
    expect(screen.getAllByText("—")).toHaveLength(2);
    expect(screen.queryByText(/NaN|Infinity/)).not.toBeInTheDocument();
  });

  it("requests the selected reporting period", async () => {
    const user = userEvent.setup();
    const fetchMock = installAnalyticsApi();
    renderApp("/app/analytics");
    await screen.findByText("Follow-up Specialist");

    await user.selectOptions(screen.getByLabelText("Reporting period"), "7");

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/api/operator/analytics?days=7"))).toBe(true);
    });
  });

  it("shows a safe API error state", async () => {
    installAnalyticsApi(mockJson({ detail: "internal details" }, 500));
    renderApp("/app/analytics");

    expect(await screen.findByRole("alert", {}, { timeout: 3000 })).toHaveTextContent(
      "Unable to load analytics for this workspace."
    );
    expect(screen.queryByText("internal details")).not.toBeInTheDocument();
  });
});
