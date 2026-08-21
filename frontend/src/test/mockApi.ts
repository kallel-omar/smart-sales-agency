import type { Mock } from "vitest";
import type { OperatorAnalyticsRead } from "../types/api";

type FetchMock = Mock<typeof fetch>;

const jsonHeaders = { "Content-Type": "application/json" };

export function mockJson(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), { status, headers: jsonHeaders });
}

export function installFetchMock(handler: (url: string, init?: RequestInit) => Response | Promise<Response>) {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    return Promise.resolve(handler(url, init));
  }) as FetchMock;
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

export const fixtures = {
  token: { access_token: "test-access-token", token_type: "bearer", expires_in: 3600 },
  user: {
    id: "user-1",
    email: "operator@example.test",
    display_name: "Operator",
    active: true,
    created_at: "2026-08-12T00:00:00Z",
    updated_at: "2026-08-12T00:00:00Z"
  },
  workspaces: [
    {
      id: "workspace-1",
      slug: "workspace-a",
      name: "Workspace A",
      active: true,
      created_at: "2026-08-12T00:00:00Z",
      updated_at: "2026-08-12T00:00:00Z"
    },
    {
      id: "workspace-2",
      slug: "workspace-b",
      name: "Workspace B",
      active: true,
      created_at: "2026-08-12T00:00:00Z",
      updated_at: "2026-08-12T00:00:00Z"
    }
  ]
};

export const analyticsFixture: OperatorAnalyticsRead = {
  period: { days: 30, starts_at: "2026-07-22T00:00:00Z", ends_at: "2026-08-21T00:00:00Z" },
  workitems: {
    current: { created: 0, assigned: 1, running: 2, waiting: 0, approval_required: 1, completed: 8, failed: 2, cancelled: 0, expired: 0 },
    created: 12, completed: 8, failed: 2, success_rate: 0.8, average_completion_seconds: 90,
    by_work_type: [{ key: "sales_follow_up", total: 10, completed: 8, failed: 2, success_rate: 0.8 }]
  },
  workforce: [{ employee_id: "employee-1", name: "Follow-up Specialist", role: "follow_up", department: "sales", workitems: 10, completed: 8, failed: 2, success_rate: 0.8, invocation_count: 9, input_tokens: 900, output_tokens: 300, total_tokens: 1200, known_estimated_cost: "0.45000000", unknown_pricing_invocation_count: 0 }],
  capabilities: [{ capability_id: "capability-1", key: "follow_up_lead", workitems: 10, completed: 8, failed: 2, success_rate: 0.8, invocation_count: 9, total_tokens: 1200, known_estimated_cost: "0.45000000", unknown_pricing_invocation_count: 0 }],
  approvals: { requests_created: 3, pending: 1, approved: 2, rejected: 0, workitems_with_approval_request: 3, approval_request_rate: 0.25 },
  ai_usage: { invocation_count: 9, input_tokens: 900, output_tokens: 300, total_tokens: 1200, known_estimated_cost: "0.45000000", unknown_pricing_invocation_count: 0, by_provider: [{ key: "openai", invocation_count: 9, input_tokens: 900, output_tokens: 300, total_tokens: 1200, known_estimated_cost: "0.45000000", unknown_pricing_invocation_count: 0 }], by_model: [{ key: "gpt-test", invocation_count: 9, input_tokens: 900, output_tokens: 300, total_tokens: 1200, known_estimated_cost: "0.45000000", unknown_pricing_invocation_count: 0 }] },
  sales: { total_leads: 20, leads_created: 5, won_leads: 2, by_status: { new: 3, qualified: 7, won: 2, lost: 1 }, outcomes: { capture_lead_completed: 4, qualification_completed: 3, follow_up_completed: 8 } }
};
