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

const statuses: WorkItemStatus[] = ["created", "assigned", "running", "waiting", "approval_required", "completed", "failed", "cancelled", "expired"];

export function WorkItemsPage() {
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

  return <div><PageHeader eyebrow="Operations" title="WorkItems" description="Track authoritative work state, assignment, business results, and failures without changing lifecycle state." action={<Badge tone="blue">{selectedWorkspace?.name ?? "No workspace"}</Badge>} />
    <div className="space-y-5 p-5 sm:p-7">
      <Card className="grid gap-3 p-4 sm:grid-cols-2 xl:grid-cols-5">
        <Filter label="Status" value={status} onChange={setStatus} options={statuses.map((value) => [value, label(value)])} />
        <Filter label="Work type" value={workType} onChange={setWorkType} options={[...new Set(options.map((item) => item.work_type))].map((value) => [value, label(value)])} />
        <Filter label="Department" value={department} onChange={setDepartment} options={uniqueOptions(options, "department_id", "department")} />
        <Filter label="AI employee" value={employee} onChange={setEmployee} options={uniqueOptions(options, "ai_employee_id", "ai_employee_name")} />
        <Filter label="Capability" value={capability} onChange={setCapability} options={uniqueOptions(options, "capability_id", "capability_key")} />
      </Card>
      {query.isLoading ? <LoadingState label="Loading WorkItems" /> : null}{query.error ? <ErrorState description="Unable to load WorkItems for this workspace." /> : null}
      {!query.isLoading && !query.error && !items.length ? <EmptyState icon={ClipboardList} title="No work items yet." description="WorkItems will appear here as HIRI departments create operational work." /> : null}
      {items.length ? <Card className="overflow-hidden"><div className="overflow-x-auto"><table className="min-w-full divide-y divide-slate-200 text-left text-sm"><thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500"><tr>{["Title", "Status", "Department", "AI employee", "Capability", "Updated"].map((heading) => <th key={heading} className="px-4 py-3 font-semibold">{heading}</th>)}</tr></thead><tbody className="divide-y divide-slate-100">{items.map((item) => <tr key={item.id} className="cursor-pointer hover:bg-slate-50" onClick={() => setSelectedId(item.id)}><td className="px-4 py-4"><button className="text-left font-semibold text-brand-700">{item.title}</button><p className="mt-1 text-xs text-slate-500">{label(item.work_type)}</p></td><td className="px-4 py-4"><WorkItemStatusBadge status={item.status} /></td><td className="px-4 py-4">{label(item.department)}</td><td className="px-4 py-4">{item.ai_employee_name ?? "Unassigned"}</td><td className="px-4 py-4">{item.capability_key ? label(item.capability_key) : "—"}</td><td className="whitespace-nowrap px-4 py-4 text-slate-500">{formatDate(item.updated_at)}</td></tr>)}</tbody></table></div></Card> : null}
    </div>{selected ? <WorkItemDetail item={selected} onClose={() => setSelectedId(null)} /> : null}</div>;
}

function WorkItemDetail({ item, onClose }: { item: OperatorWorkItemRead; onClose: () => void }) {
  return <div className="fixed inset-0 z-40 flex justify-end bg-slate-950/40" role="dialog" aria-modal="true" aria-label="WorkItem details"><button className="absolute inset-0" aria-label="Close WorkItem details" onClick={onClose} /><aside className="relative h-full w-full max-w-2xl overflow-y-auto bg-white p-5 shadow-2xl sm:p-7"><div className="flex items-start justify-between gap-4"><div><p className="text-xs font-semibold uppercase tracking-wide text-brand-700">WorkItem detail</p><h2 className="mt-1 text-xl font-semibold">{item.title}</h2><div className="mt-3"><WorkItemStatusBadge status={item.status} /></div></div><Button variant="ghost" className="px-3" aria-label="Close details" onClick={onClose}><X className="h-5 w-5" /></Button></div><div className="mt-6 space-y-6"><DefinitionList values={{ work_type: item.work_type, department: item.department, ai_employee: item.ai_employee_name, capability: item.capability_key, correlation_id: item.correlation_id, created: formatDate(item.created_at), updated: formatDate(item.updated_at), started: formatDate(item.started_at), completed: formatDate(item.completed_at), approval_status: item.approval_status, parent_work_item_id: item.parent_work_item_id, source_follow_up_task_id: item.source_follow_up_task_id }} /><DetailSection title="Input" data={item.input} /><DetailSection title="Business result" data={item.result} />{item.error_code || item.error_message ? <div className="rounded-lg border border-red-200 bg-red-50 p-4"><h3 className="font-semibold text-red-900">Failure</h3><p className="mt-2 text-sm text-red-800">{item.error_code ? `${label(item.error_code)}: ` : ""}{item.error_message}</p></div> : null}</div></aside></div>;
}

function DetailSection({ title, data }: { title: string; data: Record<string, unknown> | null }) { return <section><h3 className="mb-3 font-semibold text-slate-950">{title}</h3>{data ? <DefinitionList values={data} /> : <p className="text-sm text-slate-500">No {title.toLowerCase()} available.</p>}</section>; }
function Filter({ label: text, value, onChange, options }: { label: string; value: string; onChange: (value: string) => void; options: [string, string][] }) { return <label className="text-sm font-medium text-slate-700">{text}<select value={value} onChange={(event) => onChange(event.target.value)} className="mt-1 min-h-10 w-full rounded-md border border-slate-200 bg-white px-3"><option value="">All</option>{options.map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></label>; }
function uniqueOptions(items: OperatorWorkItemRead[], idKey: "department_id" | "ai_employee_id" | "capability_id", nameKey: "department" | "ai_employee_name" | "capability_key"): [string, string][] { return [...new Map(items.filter((item) => item[idKey] && item[nameKey]).map((item) => [item[idKey] as string, label(item[nameKey] as string)])).entries()]; }
