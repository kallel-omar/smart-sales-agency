import type { WorkItemStatus } from "../../types/api";
import { Badge } from "../ui/Badge";

const statusTone: Record<WorkItemStatus, "blue" | "green" | "amber" | "slate" | "red"> = {
  created: "slate",
  assigned: "blue",
  running: "blue",
  waiting: "amber",
  approval_required: "amber",
  completed: "green",
  failed: "red",
  cancelled: "slate",
  expired: "slate"
};

export function WorkItemStatusBadge({ status }: { status: WorkItemStatus }) {
  return <Badge tone={statusTone[status]}>{label(status)}</Badge>;
}

export function ApprovalStatusBadge({ status }: { status: string }) {
  const tone = status === "pending" ? "amber" : status === "rejected" ? "red" : "green";
  return <Badge tone={tone}>{label(status)}</Badge>;
}

export function DefinitionList({ values }: { values: Record<string, unknown> }) {
  const entries = Object.entries(values).filter(([, value]) => value !== null && value !== undefined && value !== "");
  if (!entries.length) return <p className="text-sm text-slate-500">No additional data.</p>;
  return (
    <dl className="grid gap-3 sm:grid-cols-2">
      {entries.map(([key, value]) => (
        <div key={key} className="rounded-md border border-slate-200 bg-slate-50 p-3">
          <dt className="text-xs font-semibold uppercase tracking-wide text-slate-500">{label(key)}</dt>
          <dd className="mt-1 break-words text-sm text-slate-800">{displayValue(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

export function formatDate(value: string | null | undefined) {
  return value ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "—";
}

export function label(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function displayValue(value: unknown): React.ReactNode {
  if (Array.isArray(value)) return value.length ? value.map(String).join(", ") : "None";
  if (typeof value === "object" && value !== null) {
    return <DefinitionList values={value as Record<string, unknown>} />;
  }
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return String(value);
}
