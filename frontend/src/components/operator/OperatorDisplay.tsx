import type { WorkItemStatus } from "../../types/api";
import { Badge } from "../ui/Badge";
import { useAppExperience } from "../../app/AppExperience";

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
  const { t } = useAppExperience();
  const key = status === "created" ? "createdStatus" : status === "completed" ? "completedStatus" : status === "failed" ? "failedStatus" : status;
  return <Badge tone={statusTone[status]}><span className="me-1.5 h-1.5 w-1.5 rounded-full bg-current" aria-hidden="true" />{t(key)}</Badge>;
}

export function ApprovalStatusBadge({ status }: { status: string }) {
  const { t } = useAppExperience();
  const tone = status === "pending" ? "amber" : status === "rejected" ? "red" : "green";
  return <Badge tone={tone}>{t(status) === status ? label(status) : t(status)}</Badge>;
}

export function DefinitionList({ values }: { values: Record<string, unknown> }) {
  const { t } = useAppExperience();
  const entries = Object.entries(values).filter(([, value]) => value !== null && value !== undefined && value !== "");
  if (!entries.length) return <p className="text-sm text-slate-500">{t("noAdditionalData")}</p>;
  return (
    <dl className="grid gap-3 sm:grid-cols-2">
      {entries.map(([key, value]) => (
        <div key={key} className="rounded-md border border-slate-200 bg-slate-50 p-3">
          <dt className="text-xs font-semibold text-slate-500">{(() => { const translated = ({ ai_employee: "aiEmployee", capability: "capability", department: "department", status: "status", created: "created", updated: "updated", started: "started", completed: "completed", cost: "cost", requested_action: "requestedAction", channel: "channel", lead: "lead", company: "company", work_type: "workType", work_item_status: "status", integration: "integration", requested_at: "requestedAt", reviewer_note: "reviewerNoteLabel", decided_at: "decidedAt", action: "action", autonomy: "autonomy", provider: "provider", correlation_id: "correlationId", approval_status: "approvalStatus", parent_work_item_id: "parentWorkItemId", source_follow_up_task_id: "sourceFollowUpTaskId", access_active: "accessActive" } as Record<string, string>)[key]; return translated ? t(translated) : label(key); })()}</dt>
          <dd className="bidi-data mt-1 break-words text-sm text-slate-800" dir="auto">{displayValue(value, t)}</dd>
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

function displayValue(value: unknown, t: (key: string, values?: Record<string, string | number>) => string): React.ReactNode {
  if (Array.isArray(value)) return value.length ? value.map(String).join(", ") : t("none");
  if (typeof value === "object" && value !== null) {
    return <DefinitionList values={value as Record<string, unknown>} />;
  }
  if (typeof value === "boolean") return value ? t("yes") : t("no");
  return String(value);
}
