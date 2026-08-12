export const queryKeys = {
  me: ["auth", "me"] as const,
  workspaces: ["workspaces"] as const,
  dashboard: (workspaceSlug: string) => ["dashboard", workspaceSlug] as const,
  leads: (workspaceSlug: string) => ["leads", workspaceSlug] as const,
  approvals: (workspaceSlug: string) => ["approvals", workspaceSlug] as const,
  integrations: (workspaceSlug: string) => ["integrations", workspaceSlug] as const,
  integrationSummary: (workspaceSlug: string) => ["integrations", workspaceSlug, "summary"] as const,
  aiUsageSummary: (workspaceSlug: string) => ["ai", workspaceSlug, "usage-summary"] as const
};
