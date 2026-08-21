import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderApp } from "./test/renderApp";
import { fixtures, installFetchMock, mockJson } from "./test/mockApi";

const employee = {
  id: "employee-1", name: "Follow-up Specialist", role_key: "follow_up", active: true,
  department_id: "department-1", department: "sales", created_at: "2026-08-20T09:00:00Z", updated_at: "2026-08-20T09:00:00Z",
  capabilities: [{ id: "capability-1", assignment_id: "assignment-1", key: "send_message", active: true, tool_access: [{ integration_account_id: "account-1", provider: "whatsapp_cloud", external_account_id: "Sales WhatsApp", action_type: "send_message", autonomy_level: "draft_requires_approval", active: true }] }]
};

const workItem = {
  id: "work-1", title: "Send proposal follow-up", work_type: "sales_follow_up_message", status: "approval_required",
  department_id: "department-1", department: "sales", ai_employee_id: "employee-1", ai_employee_name: "Follow-up Specialist",
  capability_id: "capability-1", capability_key: "send_message", correlation_id: "correlation-1",
  input: { message: "Here is the proposed follow-up.", channel: "whatsapp" }, result: null, error_code: null, error_message: null,
  source_follow_up_task_id: "task-1", parent_work_item_id: "parent-1", approval_id: "approval-1", approval_status: "pending",
  created_at: "2026-08-20T10:00:00Z", updated_at: "2026-08-20T10:05:00Z", started_at: "2026-08-20T10:01:00Z", completed_at: null, expires_at: null
};

const approval = {
  id: "approval-1", status: "pending", action_type: "send_message", channel: "whatsapp", payload: { message: "Here is the proposed follow-up.", recipient: "+216••••1234" },
  reviewer_note: null, created_at: "2026-08-20T10:02:00Z", decided_at: null, lead_id: "lead-1", lead_name: "Amina", company_name: "Northwind",
  work_item_id: "work-1", work_item_title: "Send proposal follow-up", work_type: "sales_follow_up_message", work_item_status: "approval_required",
  ai_employee_name: "Follow-up Specialist", capability_key: "send_message", integration_provider: "whatsapp_cloud", integration_external_account_id: "Sales WhatsApp"
};

function installOperatorApi() {
  return installFetchMock((url, init) => {
    if (url.endsWith("/api/auth/me")) return mockJson(fixtures.user);
    if (url.endsWith("/api/workspaces")) return mockJson(fixtures.workspaces);
    if (url.includes("/api/operator/workforce")) return mockJson([employee]);
    if (url.includes("/api/operator/work-items")) return mockJson([workItem]);
    if (url.includes("/api/operator/approvals")) return mockJson([approval]);
    if (url.endsWith("/api/approvals/approval-1/approve") && init?.method === "POST") return mockJson({ ...approval, status: "approved" });
    return mockJson({ detail: "Unhandled test route" }, 500);
  });
}

describe("operator pages", () => {
  beforeEach(() => { localStorage.clear(); localStorage.setItem("hiri.auth.accessToken", "test-access-token"); });
  afterEach(() => vi.unstubAllGlobals());

  it("renders persisted workforce capability and autonomy data", async () => {
    installOperatorApi(); renderApp("/app/workforce");
    expect(await screen.findByText("Follow-up Specialist")).toBeInTheDocument();
    expect(screen.getAllByText("Send Message").length).toBeGreaterThan(0);
    expect(screen.getByText("Draft Requires Approval")).toBeInTheDocument();
    expect(screen.getByText("Sales WhatsApp")).toBeInTheDocument();
  });

  it("filters WorkItems and opens readable detail without lifecycle controls", async () => {
    const user = userEvent.setup(); const fetchMock = installOperatorApi(); renderApp("/app/work-items");
    await user.selectOptions(await screen.findByLabelText("Status"), "approval_required");
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("status=approval_required"))).toBe(true);
    await user.click(screen.getByText("Send proposal follow-up"));
    const dialog = await screen.findByRole("dialog", { name: /workitem details/i });
    expect(within(dialog).getByText("Here is the proposed follow-up.")).toBeInTheDocument();
    expect(within(dialog).getByText("Correlation ID")).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: /complete|fail|run/i })).not.toBeInTheDocument();
  });

  it("reviews and approves through the existing decision endpoint with honest feedback", async () => {
    const user = userEvent.setup(); const fetchMock = installOperatorApi(); renderApp("/app/approvals");
    await user.click(await screen.findByRole("button", { name: /review details/i }));
    const dialog = screen.getByRole("dialog", { name: /approval details/i });
    expect(within(dialog).getByText("Here is the proposed follow-up.")).toBeInTheDocument();
    await user.type(within(dialog).getByLabelText(/reviewer note/i), "Looks correct");
    await user.click(within(dialog).getByRole("button", { name: "Approve" }));
    expect(await screen.findByRole("status")).toHaveTextContent("may still require backend execution");
    const request = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/api/approvals/approval-1/approve"));
    expect(request).toBeDefined();
    expect(JSON.parse(request?.[1]?.body as string)).toEqual({ reviewer_note: "Looks correct" });
  });
});
