import { useQuery } from "@tanstack/react-query";
import { ClipboardList, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { useAuth } from "../auth/AuthProvider";
import { DefinitionList, formatDate, label, WorkItemStatusBadge } from "../components/operator/OperatorDisplay";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { LoadingState } from "../components/ui/LoadingState";
import { PageHeader } from "../components/ui/PageHeader";
import { apiClient } from "../lib/api";
import { queryKeys } from "../lib/queryKeys";
import type { OperatorWorkItemRead, WorkItemStatus } from "../types/api";
import { useWorkspace } from "../workspaces/WorkspaceProvider";
import { useAppExperience } from "../app/AppExperience";

const statuses: WorkItemStatus[] = ["created", "assigned", "running", "waiting", "approval_required", "completed", "failed", "cancelled", "expired"];

export function WorkItemsPage() {
  const { t } = useAppExperience();
  const { token } = useAuth();
  const { selectedWorkspace, selectedWorkspaceSlug } = useWorkspace();
  const [status, setStatus] = useState("");
  const [workType, setWorkType] = useState("");
  const [department, setDepartment] = useState("");
  const [employee, setEmployee] = useState("");
  const [capability, setCapability] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  useEffect(() => setSelectedId(null), [selectedWorkspaceSlug]);
  const query = useQuery({
    queryKey: queryKeys.operatorWorkItems(selectedWorkspaceSlug ?? "none", status, workType),
    queryFn: () => apiClient.operatorWorkItems(token as string, selectedWorkspaceSlug as string, { status, workType }),
    enabled: Boolean(token && selectedWorkspaceSlug)
  });
  const items = useMemo(() => (query.data ?? []).filter((item) => (!department || item.department_id === department) && (!employee || item.ai_employee_id === employee) && (!capability || item.capability_id === capability)), [query.data, department, employee, capability]);
  const selected = query.data?.find((item) => item.id === selectedId) ?? null;
  const options = query.data ?? [];

  return <div><PageHeader eyebrow={t("operations")} title={t("workItems")} description={t("workItemsDescription")} action={<Badge tone="blue">{selectedWorkspace?.name ?? t("noWorkspace")}</Badge>} />
    <div className="space-y-5 p-5 sm:p-7">
      <Card className="p-4"><p className="mb-3 text-xs font-semibold text-slate-500">{t("filters")}</p><div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <Filter label={t("status")} value={status} onChange={setStatus} options={statuses.map((value) => [value, t(value === "created" ? "createdStatus" : value === "completed" ? "completedStatus" : value === "failed" ? "failedStatus" : value)])} allLabel={t("all")} />
        <Filter label={t("workType")} value={workType} onChange={setWorkType} options={[...new Set(options.map((item) => item.work_type))].map((value) => [value, label(value)])} allLabel={t("all")} />
        <Filter label={t("department")} value={department} onChange={setDepartment} options={uniqueOptions(options, "department_id", "department")} allLabel={t("all")} />
        <Filter label={t("aiEmployee")} value={employee} onChange={setEmployee} options={uniqueOptions(options, "ai_employee_id", "ai_employee_name")} allLabel={t("all")} />
        <Filter label={t("capability")} value={capability} onChange={setCapability} options={uniqueOptions(options, "capability_id", "capability_key")} allLabel={t("all")} />
      </div>
      </Card>
      {query.isLoading ? <LoadingState label={t("loadingWorkItems")} /> : null}{query.error ? <ErrorState description={t("unableLoadWorkItems")} /> : null}
      {!query.isLoading && !query.error && !items.length ? <EmptyState icon={ClipboardList} title={t("noWorkItems")} description={t("noWorkItemsDescription")} /> : null}
      {items.length ? <Card className="overflow-hidden"><div className="overflow-x-auto"><table className="min-w-full divide-y divide-slate-200 text-sm"><thead className="bg-slate-50 text-xs text-slate-500"><tr>{[t("title"), t("status"), t("department"), t("aiEmployee"), t("capability"), t("updated")].map((heading) => <th key={heading} className="px-4 py-3 font-semibold">{heading}</th>)}</tr></thead><tbody className="divide-y divide-slate-100">{items.map((item) => <tr key={item.id} className="cursor-pointer hover:bg-slate-50" onClick={() => setSelectedId(item.id)}><td className="px-4 py-4"><button className="bidi-data font-semibold text-brand-700" dir="auto">{item.title}</button><p className="mt-1 text-xs text-slate-500">{label(item.work_type)}</p></td><td className="px-4 py-4"><WorkItemStatusBadge status={item.status} /></td><td className="px-4 py-4">{label(item.department)}</td><td className="bidi-data px-4 py-4" dir="auto">{item.ai_employee_name ?? t("unassigned")}</td><td className="px-4 py-4">{item.capability_key ? label(item.capability_key) : "—"}</td><td className="whitespace-nowrap px-4 py-4 text-slate-500">{formatDate(item.updated_at)}</td></tr>)}</tbody></table></div></Card> : null}
    </div>{selected ? <WorkItemDetail item={selected} onClose={() => setSelectedId(null)} /> : null}</div>;
}

function WorkItemDetail({ item, onClose }: { item: OperatorWorkItemRead; onClose: () => void }) {
  const { t } = useAppExperience();
  return <div className="fixed inset-0 z-40 flex justify-end bg-slate-950/40" role="dialog" aria-modal="true" aria-label={t("workItemDetails")}><button className="absolute inset-0" aria-label={t("closeWorkItemDetails")} onClick={onClose} /><aside className="relative h-full w-full max-w-2xl overflow-y-auto bg-white p-5 shadow-2xl sm:p-7"><div className="flex items-start justify-between gap-4"><div><p className="text-xs font-semibold uppercase tracking-wide text-brand-700">{t("workItemDetail")}</p><h2 className="bidi-data mt-1 text-xl font-semibold" dir="auto">{item.title}</h2><div className="mt-3"><WorkItemStatusBadge status={item.status} /></div></div><Button variant="ghost" className="px-3" aria-label={t("closeDetails")} onClick={onClose}><X className="h-5 w-5" /></Button></div><div className="mt-6 space-y-6"><DefinitionList values={{ work_type: item.work_type, department: item.department, ai_employee: item.ai_employee_name, capability: item.capability_key, correlation_id: item.correlation_id, created: formatDate(item.created_at), updated: formatDate(item.updated_at), started: formatDate(item.started_at), completed: formatDate(item.completed_at), approval_status: item.approval_status, parent_work_item_id: item.parent_work_item_id, source_follow_up_task_id: item.source_follow_up_task_id }} /><DetailSection title={t("input")} data={item.input} /><DetailSection title={t("businessResult")} data={item.result} />{item.error_code || item.error_message ? <div className="rounded-lg border border-red-200 bg-red-50 p-4"><h3 className="font-semibold text-red-900">{t("failure")}</h3><p className="bidi-data mt-2 text-sm text-red-800" dir="auto">{item.error_code ? `${label(item.error_code)}: ` : ""}{item.error_message}</p></div> : null}</div></aside></div>;
}

function DetailSection({ title, data }: { title: string; data: Record<string, unknown> | null }) { const { t } = useAppExperience(); return <section><h3 className="mb-3 font-semibold text-slate-950">{title}</h3>{data ? <DefinitionList values={data} /> : <p className="text-sm text-slate-500">{t("noDataAvailable", { section: title })}</p>}</section>; }
function Filter({ label: text, value, onChange, options, allLabel }: { label: string; value: string; onChange: (value: string) => void; options: [string, string][]; allLabel: string }) { return <label className="text-sm font-medium text-slate-700">{text}<select value={value} onChange={(event) => onChange(event.target.value)} className="mt-1 min-h-10 w-full rounded-md border border-slate-200 bg-white px-3"><option value="">{allLabel}</option>{options.map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></label>; }
function uniqueOptions(items: OperatorWorkItemRead[], idKey: "department_id" | "ai_employee_id" | "capability_id", nameKey: "department" | "ai_employee_name" | "capability_key"): [string, string][] { return [...new Map(items.filter((item) => item[idKey] && item[nameKey]).map((item) => [item[idKey] as string, label(item[nameKey] as string)])).entries()]; }
