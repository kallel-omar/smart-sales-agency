import { useQueries } from "@tanstack/react-query";
import { AlertCircle, Bot, CheckSquare, CircleAlert, PlayCircle, UsersRound } from "lucide-react";

import { useAuth } from "../auth/AuthProvider";
import { Badge } from "../components/ui/Badge";
import { Card } from "../components/ui/Card";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { LoadingState } from "../components/ui/LoadingState";
import { MetricCard } from "../components/ui/MetricCard";
import { PageHeader } from "../components/ui/PageHeader";
import { apiClient } from "../lib/api";
import { queryKeys } from "../lib/queryKeys";
import { useWorkspace } from "../workspaces/WorkspaceProvider";

export function DashboardPage() {
  const { token, user } = useAuth();
  const { selectedWorkspace, selectedWorkspaceSlug, isLoading, error } = useWorkspace();

  const [leadsQuery, approvalsQuery, integrationsQuery, operationsQuery, aiUsageQuery, workforceQuery, workItemsQuery] = useQueries({
    queries: [
      {
        queryKey: queryKeys.leads(selectedWorkspaceSlug ?? "none"),
        queryFn: () => apiClient.leads(token as string, selectedWorkspaceSlug as string),
        enabled: Boolean(token && selectedWorkspaceSlug)
      },
      {
        queryKey: queryKeys.approvals(selectedWorkspaceSlug ?? "none"),
        queryFn: () => apiClient.approvals(token as string, selectedWorkspaceSlug as string),
        enabled: Boolean(token && selectedWorkspaceSlug)
      },
      {
        queryKey: queryKeys.integrations(selectedWorkspaceSlug ?? "none"),
        queryFn: () => apiClient.integrationAccounts(token as string, selectedWorkspaceSlug as string),
        enabled: Boolean(token && selectedWorkspaceSlug)
      },
      {
        queryKey: queryKeys.integrationSummary(selectedWorkspaceSlug ?? "none"),
        queryFn: () => apiClient.integrationSummary(token as string, selectedWorkspaceSlug as string),
        enabled: Boolean(token && selectedWorkspaceSlug)
      },
      {
        queryKey: queryKeys.aiUsageSummary(selectedWorkspaceSlug ?? "none"),
        queryFn: () => apiClient.aiUsageSummary(token as string, selectedWorkspaceSlug as string),
        enabled: Boolean(token && selectedWorkspaceSlug)
      },
      {
        queryKey: queryKeys.operatorWorkforce(selectedWorkspaceSlug ?? "none"),
        queryFn: () => apiClient.operatorWorkforce(token as string, selectedWorkspaceSlug as string),
        enabled: Boolean(token && selectedWorkspaceSlug)
      },
      {
        queryKey: queryKeys.operatorWorkItems(selectedWorkspaceSlug ?? "none"),
        queryFn: () => apiClient.operatorWorkItems(token as string, selectedWorkspaceSlug as string),
        enabled: Boolean(token && selectedWorkspaceSlug)
      }
    ]
  });

  if (isLoading) {
    return <LoadingState label="Loading dashboard" />;
  }

  if (error) {
    return (
      <div className="p-6">
        <ErrorState description="Unable to load your accessible workspaces." />
      </div>
    );
  }

  if (!selectedWorkspace) {
    return (
      <div className="p-6">
        <EmptyState
          icon={AlertCircle}
          title="No workspace available"
          description="Create or join a workspace before using the operating dashboard."
        />
      </div>
    );
  }

  const pendingApprovals = approvalsQuery.data?.filter((approval) => approval.status === "pending") ?? [];
  const activeIntegrations = integrationsQuery.data?.filter((account) => account.active) ?? [];
  const runningWorkItems = workItemsQuery.data?.filter((item) => item.status === "running") ?? [];
  const failedWorkItems = workItemsQuery.data?.filter((item) => item.status === "failed") ?? [];
  const dashboardLoading =
    leadsQuery.isLoading ||
    approvalsQuery.isLoading ||
    integrationsQuery.isLoading ||
    operationsQuery.isLoading ||
    aiUsageQuery.isLoading || workforceQuery.isLoading || workItemsQuery.isLoading;
  const dashboardError =
    leadsQuery.error || approvalsQuery.error || integrationsQuery.error || operationsQuery.error || aiUsageQuery.error || workforceQuery.error || workItemsQuery.error;

  return (
    <div>
      <PageHeader
        eyebrow="Overview"
        title={`Good to see you${user?.display_name ? `, ${user.display_name}` : ""}`}
        description={`${selectedWorkspace.name} is selected. This shell reads only FastAPI APIs and uses workspace slug headers for scoped requests.`}
        action={<Badge tone={selectedWorkspace.active ? "green" : "amber"}>{selectedWorkspace.active ? "Active" : "Inactive"}</Badge>}
      />

      <div className="space-y-6 p-5 sm:p-7">
        {dashboardLoading ? <LoadingState label="Loading dashboard data" /> : null}
        {dashboardError ? (
          <ErrorState description="One or more dashboard sections could not be loaded. No sensitive backend details are shown here." />
        ) : null}

        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <MetricCard icon={Bot} label="AI employees" value={workforceQuery.data?.length ?? 0} helper="Configured workforce" />
          <MetricCard icon={PlayCircle} label="Running WorkItems" value={runningWorkItems.length} helper="In progress" />
          <MetricCard icon={CheckSquare} label="Approval required" value={pendingApprovals.length} helper="Human gate" />
          <MetricCard icon={CircleAlert} label="Failed WorkItems" value={failedWorkItems.length} helper="Needs attention" />
        </div>

        <div className="grid gap-5 xl:grid-cols-[1.3fr_0.7fr]">
          <Card className="p-5">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h2 className="text-base font-semibold text-slate-950">Recent operational state</h2>
                <p className="mt-1 text-sm text-slate-600">Safe aggregates from FastAPI, not generated facts.</p>
              </div>
              <Badge tone="blue">Sales department</Badge>
            </div>

            <div className="mt-5 grid gap-3 sm:grid-cols-3">
              <div className="rounded-md border border-slate-200 p-4">
                <p className="text-sm text-slate-500">Delivered actions</p>
                <p className="mt-2 text-xl font-semibold text-slate-950">
                  {operationsQuery.data?.delivered_outbound_action_count ?? 0}
                </p>
              </div>
              <div className="rounded-md border border-slate-200 p-4">
                <p className="text-sm text-slate-500">Failed actions</p>
                <p className="mt-2 text-xl font-semibold text-slate-950">
                  {operationsQuery.data?.failed_outbound_action_count ?? 0}
                </p>
              </div>
              <div className="rounded-md border border-slate-200 p-4">
                <p className="text-sm text-slate-500">Estimated AI spend</p>
                <p className="mt-2 text-xl font-semibold text-slate-950">
                  {aiUsageQuery.data?.known_estimated_spend ?? "0"}
                </p>
              </div>
            </div>

            {!dashboardLoading && !dashboardError && (leadsQuery.data?.length ?? 0) === 0 ? (
              <div className="mt-5">
                <EmptyState
                  icon={UsersRound}
                  title="No leads yet"
                  description="When workspace leads exist, this dashboard will summarize them from FastAPI."
                />
              </div>
            ) : null}
          </Card>

          <Card className="p-5">
            <h2 className="text-base font-semibold text-slate-950">Workspace readiness</h2>
            <div className="mt-5 space-y-3">
              <ReadinessRow label="Workspace selected" ready={Boolean(selectedWorkspaceSlug)} />
              <ReadinessRow label="Integration account active" ready={activeIntegrations.length > 0} />
              <ReadinessRow label="Approval queue available" ready={!approvalsQuery.isError} />
              <ReadinessRow label="AI usage endpoint available" ready={!aiUsageQuery.isError} />
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}

function ReadinessRow({ label, ready }: { label: string; ready: boolean }) {
  return (
    <div className="flex items-center justify-between gap-4 rounded-md border border-slate-200 px-3 py-3">
      <span className="text-sm font-medium text-slate-700">{label}</span>
      <Badge tone={ready ? "green" : "slate"}>{ready ? "Ready" : "Empty"}</Badge>
    </div>
  );
}
