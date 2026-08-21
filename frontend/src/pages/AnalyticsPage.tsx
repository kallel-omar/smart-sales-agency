import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  Bot,
  BrainCircuit,
  CheckCircle2,
  CircleX,
  Coins,
  ShieldCheck,
  UsersRound
} from "lucide-react";
import { useState } from "react";

import { useAuth } from "../auth/AuthProvider";
import { label } from "../components/operator/OperatorDisplay";
import { Badge } from "../components/ui/Badge";
import { Card } from "../components/ui/Card";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { LoadingState } from "../components/ui/LoadingState";
import { MetricCard } from "../components/ui/MetricCard";
import { PageHeader } from "../components/ui/PageHeader";
import { apiClient } from "../lib/api";
import { queryKeys } from "../lib/queryKeys";
import type { AnalyticsDays } from "../types/api";
import { useWorkspace } from "../workspaces/WorkspaceProvider";

const periods: AnalyticsDays[] = [7, 30, 90];

export function AnalyticsPage() {
  const { token } = useAuth();
  const { selectedWorkspace, selectedWorkspaceSlug } = useWorkspace();
  const [days, setDays] = useState<AnalyticsDays>(30);
  const query = useQuery({
    queryKey: queryKeys.operatorAnalytics(selectedWorkspaceSlug ?? "none", days),
    queryFn: () =>
      apiClient.operatorAnalytics(token as string, selectedWorkspaceSlug as string, days),
    enabled: Boolean(token && selectedWorkspaceSlug)
  });

  const data = query.data;
  const noActivity =
    data?.workitems.created === 0 &&
    data.ai_usage.invocation_count === 0 &&
    data.sales.leads_created === 0 &&
    data.approvals.requests_created === 0;

  return (
    <div>
      <PageHeader
        eyebrow="Analytics"
        title="Business and workforce analytics"
        description="Workspace-scoped operational facts from persisted WorkItems, approvals, AI accounting, and Sales leads."
        action={<Badge tone="blue">{selectedWorkspace?.name ?? "No workspace"}</Badge>}
      />
      <div className="space-y-6 p-5 sm:p-7">
        <div className="flex items-center gap-3">
          <label className="text-sm font-medium text-slate-700" htmlFor="analytics-period">
            Reporting period
          </label>
          <select
            id="analytics-period"
            className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm"
            value={days}
            onChange={(event) => setDays(Number(event.target.value) as AnalyticsDays)}
          >
            {periods.map((period) => (
              <option key={period} value={period}>
                Last {period} days
              </option>
            ))}
          </select>
        </div>

        {query.isLoading ? <LoadingState label="Loading analytics" /> : null}
        {query.error ? (
          <ErrorState description="Unable to load analytics for this workspace." />
        ) : null}
        {!query.isLoading && !query.error && noActivity ? (
          <EmptyState
            icon={Activity}
            title="No activity in this period"
            description={`No WorkItems, approvals, AI invocations, or leads were created in the last ${days} days.`}
          />
        ) : null}

        {data ? (
          <>
            <section aria-labelledby="analytics-overview">
              <SectionTitle id="analytics-overview" title="Overview" />
              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                <MetricCard icon={Activity} label="WorkItems created" value={data.workitems.created} />
                <MetricCard icon={CheckCircle2} label="Completed" value={data.workitems.completed} />
                <MetricCard icon={CircleX} label="Failed" value={data.workitems.failed} />
                <MetricCard icon={Activity} label="Success rate" value={formatRate(data.workitems.success_rate)} helper="Completed ÷ (completed + failed)" />
              </div>
            </section>

            <section aria-labelledby="analytics-control">
              <SectionTitle id="analytics-control" title="Human control" />
              <div className="grid gap-4 md:grid-cols-3">
                <MetricCard icon={ShieldCheck} label="Pending approvals" value={data.approvals.pending} />
                <MetricCard icon={ShieldCheck} label="Approval requests" value={data.approvals.requests_created} />
                <MetricCard icon={ShieldCheck} label="Approval request rate" value={formatRate(data.approvals.approval_request_rate)} helper="Distinct requested WorkItems ÷ WorkItems created" />
              </div>
            </section>

            <section aria-labelledby="analytics-ai">
              <SectionTitle id="analytics-ai" title="AI usage" />
              <div className="grid gap-4 md:grid-cols-3">
                <MetricCard icon={BrainCircuit} label="Invocations" value={data.ai_usage.invocation_count} />
                <MetricCard icon={BrainCircuit} label="Tokens" value={data.ai_usage.total_tokens.toLocaleString()} />
                <MetricCard icon={Coins} label="Known estimated cost" value={data.ai_usage.known_estimated_cost} helper={data.ai_usage.unknown_pricing_invocation_count ? `${data.ai_usage.unknown_pricing_invocation_count} invocation(s) have unknown pricing` : "Persisted accounting value"} />
              </div>
            </section>

            <div className="grid gap-6 xl:grid-cols-2">
              <AnalyticsTable
                title="Workforce activity"
                icon={Bot}
                headers={["Employee", "WorkItems", "Completed", "Failed", "Success", "Invocations", "Cost"]}
                rows={data.workforce.map((employee) => [
                  <div key={employee.employee_id}><span className="font-medium text-slate-900">{employee.name}</span><span className="block text-xs text-slate-500">{label(employee.role)} · {label(employee.department)}</span></div>,
                  employee.workitems,
                  employee.completed,
                  employee.failed,
                  formatRate(employee.success_rate),
                  employee.invocation_count,
                  employee.known_estimated_cost
                ])}
              />
              <AnalyticsTable
                title="Capability activity"
                icon={Activity}
                headers={["Capability", "WorkItems", "Completed", "Failed", "Success", "Invocations", "Cost"]}
                rows={data.capabilities.map((capability) => [
                  label(capability.key),
                  capability.workitems,
                  capability.completed,
                  capability.failed,
                  formatRate(capability.success_rate),
                  capability.invocation_count,
                  capability.known_estimated_cost
                ])}
              />
            </div>

            <section aria-labelledby="analytics-sales">
              <SectionTitle id="analytics-sales" title="Sales" />
              <div className="grid gap-4 md:grid-cols-3">
                <MetricCard icon={UsersRound} label="Total leads" value={data.sales.total_leads} />
                <MetricCard icon={UsersRound} label="Leads created" value={data.sales.leads_created} />
                <MetricCard icon={CheckCircle2} label="Won leads" value={data.sales.won_leads} helper="Persisted won status" />
              </div>
              <Card className="mt-4 p-5">
                <h3 className="text-sm font-semibold text-slate-900">Lead status breakdown</h3>
                <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
                  {Object.entries(data.sales.by_status).map(([status, count]) => (
                    <div key={status} className="rounded-md border border-slate-200 px-3 py-3">
                      <p className="text-xs font-medium text-slate-500">{label(status)}</p>
                      <p className="mt-1 text-lg font-semibold text-slate-900">{count}</p>
                    </div>
                  ))}
                </div>
              </Card>
            </section>
          </>
        ) : null}
      </div>
    </div>
  );
}

function SectionTitle({ id, title }: { id: string; title: string }) {
  return <h2 id={id} className="mb-3 text-base font-semibold text-slate-950">{title}</h2>;
}

function formatRate(value: number | null) {
  return value === null ? "—" : `${(value * 100).toFixed(1)}%`;
}

function AnalyticsTable({
  title,
  icon: Icon,
  headers,
  rows
}: {
  title: string;
  icon: typeof Bot;
  headers: string[];
  rows: React.ReactNode[][];
}) {
  return (
    <Card className="overflow-hidden">
      <div className="flex items-center gap-2 border-b border-slate-200 p-5">
        <Icon className="h-5 w-5 text-brand-600" aria-hidden="true" />
        <h2 className="font-semibold text-slate-950">{title}</h2>
      </div>
      <div className="overflow-x-auto">
        <table className="min-w-full text-left text-sm">
          <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
            <tr>{headers.map((header) => <th key={header} className="px-4 py-3 font-semibold">{header}</th>)}</tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {rows.map((row, rowIndex) => (
              <tr key={rowIndex}>{row.map((cell, cellIndex) => <td key={cellIndex} className="whitespace-nowrap px-4 py-3 text-slate-700">{cell}</td>)}</tr>
            ))}
            {!rows.length ? <tr><td className="px-4 py-5 text-slate-500" colSpan={headers.length}>No configured activity.</td></tr> : null}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
