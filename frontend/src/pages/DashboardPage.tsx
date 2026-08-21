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
import { useAppExperience } from "../app/AppExperience";

export function DashboardPage() {
  const { t } = useAppExperience();
  const { token } = useAuth();
  const { selectedWorkspace, selectedWorkspaceSlug, isLoading, error } = useWorkspace();

  const [leadsQuery, integrationsQuery, operationsQuery, analyticsQuery] = useQueries({
    queries: [
      {
        queryKey: queryKeys.leads(selectedWorkspaceSlug ?? "none"),
        queryFn: () => apiClient.leads(token as string, selectedWorkspaceSlug as string),
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
        queryKey: queryKeys.operatorAnalytics(selectedWorkspaceSlug ?? "none", 30),
        queryFn: () => apiClient.operatorAnalytics(token as string, selectedWorkspaceSlug as string, 30),
        enabled: Boolean(token && selectedWorkspaceSlug)
      }
    ]
  });

  if (isLoading) {
    return <LoadingState label={t("loadingDashboard")} />;
  }

  if (error) {
    return (
      <div className="p-6">
        <ErrorState description={t("unableLoadWorkspaces")} />
      </div>
    );
  }

  if (!selectedWorkspace) {
    return (
      <div className="p-6">
        <EmptyState
          icon={AlertCircle}
          title={t("noWorkspaceAvailable")}
          description={t("noWorkspaceDescription")}
        />
      </div>
    );
  }

  const activeIntegrations = integrationsQuery.data?.filter((account) => account.active) ?? [];
  const dashboardLoading =
    leadsQuery.isLoading ||
    integrationsQuery.isLoading ||
    operationsQuery.isLoading ||
    analyticsQuery.isLoading;
  const dashboardError =
    leadsQuery.error || integrationsQuery.error || operationsQuery.error || analyticsQuery.error;

  return (
    <div>
      <PageHeader
        eyebrow={t("overview")}
        title={t("commandCenter")}
        description={t("commandDescription")}
        action={<Badge tone={selectedWorkspace.active ? "green" : "amber"}>{selectedWorkspace.active ? t("active") : t("inactive")}</Badge>}
      />

      <div className="space-y-6 p-5 sm:p-7">
        {dashboardLoading ? <LoadingState label={t("loadingDashboardData")} /> : null}
        {dashboardError ? (
          <ErrorState description={t("dashboardSectionsError")} />
        ) : null}

        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <MetricCard icon={Bot} label={t("aiEmployees")} value={analyticsQuery.data?.workforce.length ?? 0} helper={t("configuredWorkforce")} />
          <MetricCard icon={PlayCircle} label={t("runningWork")} value={analyticsQuery.data?.workitems.current.running ?? 0} helper={t("currentState")} />
          <MetricCard icon={CheckSquare} label={t("approvalRequired")} value={analyticsQuery.data?.workitems.current.approval_required ?? 0} helper={t("currentWorkItemState")} />
          <MetricCard icon={CircleAlert} label={t("failedWork")} value={analyticsQuery.data?.workitems.current.failed ?? 0} helper={t("currentState")} />
        </div>

        <div className="grid gap-5 xl:grid-cols-[1.3fr_0.7fr]">
          <Card className="p-5">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h2 className="text-base font-semibold text-slate-950">{t("recentState")}</h2>
                <p className="mt-1 text-sm text-slate-600">{t("safeAggregates")}</p>
              </div>
              <Badge tone="blue">{t("salesDepartment")}</Badge>
            </div>

            <div className="mt-5 grid gap-3 sm:grid-cols-3">
              <div className="rounded-md border border-slate-200 p-4">
                <p className="text-sm text-slate-500">{t("deliveredActions")}</p>
                <p className="mt-2 text-xl font-semibold text-slate-950">
                  {operationsQuery.data?.delivered_outbound_action_count ?? 0}
                </p>
              </div>
              <div className="rounded-md border border-slate-200 p-4">
                <p className="text-sm text-slate-500">{t("failedActions")}</p>
                <p className="mt-2 text-xl font-semibold text-slate-950">
                  {operationsQuery.data?.failed_outbound_action_count ?? 0}
                </p>
              </div>
              <div className="rounded-md border border-slate-200 p-4">
                <p className="text-sm text-slate-500">{t("estimatedAiSpend")}</p>
                <p className="mt-2 text-xl font-semibold text-slate-950">
                  {analyticsQuery.data?.ai_usage.known_estimated_cost ?? "0"}
                </p>
              </div>
            </div>

            {!dashboardLoading && !dashboardError && (leadsQuery.data?.length ?? 0) === 0 ? (
              <div className="mt-5">
                <EmptyState
                  icon={UsersRound}
                  title={t("noLeadsYet")}
                  description={t("noLeadsDescription")}
                />
              </div>
            ) : null}
          </Card>

          <Card className="p-5">
            <h2 className="text-base font-semibold text-slate-950">{t("readiness")}</h2>
            <div className="mt-5 space-y-3">
              <ReadinessRow label={t("workspaceSelected")} ready={Boolean(selectedWorkspaceSlug)} readyLabel={t("ready")} emptyLabel={t("empty")} />
              <ReadinessRow label={t("integrationAccountActive")} ready={activeIntegrations.length > 0} readyLabel={t("ready")} emptyLabel={t("empty")} />
              <ReadinessRow label={t("approvalAnalyticsAvailable")} ready={!analyticsQuery.isError} readyLabel={t("ready")} emptyLabel={t("empty")} />
              <ReadinessRow label={t("aiUsageAnalyticsAvailable")} ready={!analyticsQuery.isError} readyLabel={t("ready")} emptyLabel={t("empty")} />
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}

function ReadinessRow({ label, ready, readyLabel, emptyLabel }: { label: string; ready: boolean; readyLabel: string; emptyLabel: string }) {
  return (
    <div className="flex items-center justify-between gap-4 rounded-md border border-slate-200 px-3 py-3">
      <span className="text-sm font-medium text-slate-700">{label}</span>
      <Badge tone={ready ? "green" : "slate"}>{ready ? readyLabel : emptyLabel}</Badge>
    </div>
  );
}
