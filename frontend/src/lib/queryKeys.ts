export const queryKeys = {
  me: ["auth", "me"] as const,
  workspaces: ["workspaces"] as const,
  dashboard: (workspaceSlug: string) => ["dashboard", workspaceSlug] as const,
  leads: (workspaceSlug: string) => ["leads", workspaceSlug] as const,
  conversation: (workspaceSlug: string, leadId: string) =>
    ["conversation", workspaceSlug, leadId] as const,
  approvals: (workspaceSlug: string) => ["approvals", workspaceSlug] as const,
  operatorWorkforce: (workspaceSlug: string) => ["operator", workspaceSlug, "workforce"] as const,
  operatorWorkItems: (workspaceSlug: string, status = "", workType = "") =>
    ["operator", workspaceSlug, "work-items", status, workType] as const,
  operatorApprovals: (workspaceSlug: string) => ["operator", workspaceSlug, "approvals"] as const,
  integrations: (workspaceSlug: string) => ["integrations", workspaceSlug] as const,
  integrationSummary: (workspaceSlug: string) => ["integrations", workspaceSlug, "summary"] as const,
  aiUsageSummary: (workspaceSlug: string) => ["ai", workspaceSlug, "usage-summary"] as const
};
