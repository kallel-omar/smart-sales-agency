import { API_BASE_URL } from "./env";
import type {
  AIInvocationUsageSummaryRead,
  AccessTokenRead,
  ApprovalRead,
  ConversationMessageRead,
  DirectSalesReply,
  IntegrationAccountRead,
  IntegrationOperationalSummaryRead,
  LeadRead,
  OperatorAIEmployeeRead,
  OperatorAnalyticsRead,
  OperatorApprovalRead,
  OperatorWorkItemRead,
  UserRead,
  WorkspaceRead
} from "../types/api";

interface RequestOptions {
  token?: string | null;
  workspaceSlug?: string | null;
  method?: "GET" | "POST" | "PUT" | "DELETE";
  body?: unknown;
  idempotencyKey?: string;
}

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, detail: unknown) {
    super(typeof detail === "string" ? detail : "Request failed");
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers = new Headers({ Accept: "application/json" });

  if (options.body !== undefined) {
    headers.set("Content-Type", "application/json");
  }
  if (options.token) {
    headers.set("Authorization", `Bearer ${options.token}`);
  }
  if (options.workspaceSlug) {
    headers.set("X-Workspace-Slug", options.workspaceSlug);
  }
  if (options.idempotencyKey) {
    headers.set("Idempotency-Key", options.idempotencyKey);
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: options.method ?? "GET",
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body)
  });

  const contentType = response.headers.get("Content-Type") ?? "";
  const payload =
    contentType.includes("application/json") && response.status !== 204
      ? await response.json()
      : null;

  if (response.status === 401) {
    window.dispatchEvent(new CustomEvent("hiri:auth-expired"));
  }
  if (!response.ok) {
    throw new ApiError(response.status, payload?.detail ?? payload ?? response.statusText);
  }
  return payload as T;
}

export const apiClient = {
  login(email: string, password: string) {
    return request<AccessTokenRead>("/api/auth/login", {
      method: "POST",
      body: { email, password }
    });
  },
  me(token: string) {
    return request<UserRead>("/api/auth/me", { token });
  },
  workspaces(token: string) {
    return request<WorkspaceRead[]>("/api/workspaces", { token });
  },
  leads(token: string, workspaceSlug: string) {
    return request<LeadRead[]>("/api/leads", { token, workspaceSlug });
  },
  conversationHistory(token: string, workspaceSlug: string, leadId: string, limit = 100) {
    return request<ConversationMessageRead[]>(`/api/conversations/${leadId}?limit=${limit}`, {
      token,
      workspaceSlug
    });
  },
  replyToConversation({
    token,
    workspaceSlug,
    leadId,
    content,
    channel,
    idempotencyKey
  }: {
    token: string;
    workspaceSlug: string;
    leadId: string;
    content: string;
    channel: string;
    idempotencyKey: string;
  }) {
    return request<DirectSalesReply>(`/api/conversations/${leadId}/reply`, {
      token,
      workspaceSlug,
      method: "POST",
      body: { channel, content },
      idempotencyKey
    });
  },
  approvals(token: string, workspaceSlug: string) {
    return request<ApprovalRead[]>("/api/approvals", { token, workspaceSlug });
  },
  operatorWorkforce(token: string, workspaceSlug: string) {
    return request<OperatorAIEmployeeRead[]>("/api/operator/workforce?limit=100", {
      token,
      workspaceSlug
    });
  },
  operatorWorkItems(
    token: string,
    workspaceSlug: string,
    filters: { status?: string; workType?: string } = {}
  ) {
    const params = new URLSearchParams({ limit: "100" });
    if (filters.status) params.set("status", filters.status);
    if (filters.workType) params.set("work_type", filters.workType);
    return request<OperatorWorkItemRead[]>(`/api/operator/work-items?${params}`, {
      token,
      workspaceSlug
    });
  },
  operatorApprovals(token: string, workspaceSlug: string) {
    return request<OperatorApprovalRead[]>("/api/operator/approvals?limit=100", {
      token,
      workspaceSlug
    });
  },
  operatorAnalytics(token: string, workspaceSlug: string, days: 7 | 30 | 90 = 30) {
    return request<OperatorAnalyticsRead>(`/api/operator/analytics?days=${days}`, {
      token,
      workspaceSlug
    });
  },
  decideApproval(
    token: string,
    workspaceSlug: string,
    approvalId: string,
    decision: "approve" | "reject",
    reviewerNote?: string
  ) {
    return request<ApprovalRead>(`/api/approvals/${approvalId}/${decision}`, {
      token,
      workspaceSlug,
      method: "POST",
      body: { reviewer_note: reviewerNote?.trim() || null }
    });
  },
  integrationAccounts(token: string, workspaceSlug: string) {
    return request<IntegrationAccountRead[]>("/api/integrations/accounts", { token, workspaceSlug });
  },
  integrationSummary(token: string, workspaceSlug: string) {
    return request<IntegrationOperationalSummaryRead>("/api/integrations/operational-summary", {
      token,
      workspaceSlug
    });
  },
  aiUsageSummary(token: string, workspaceSlug: string) {
    return request<AIInvocationUsageSummaryRead>("/api/integrations/ai-usage/summary", {
      token,
      workspaceSlug
    });
  }
};
