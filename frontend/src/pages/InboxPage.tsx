import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Building2,
  CheckCircle2,
  Clock,
  Mail,
  MessageSquareText,
  Phone,
  Send,
  ShieldAlert,
  UserRound
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { useAuth } from "../auth/AuthProvider";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { LoadingState } from "../components/ui/LoadingState";
import { PageHeader } from "../components/ui/PageHeader";
import { apiClient, ApiError } from "../lib/api";
import { queryKeys } from "../lib/queryKeys";
import type { ConversationMessageRead, DirectSalesReply, LeadRead } from "../types/api";
import { useWorkspace } from "../workspaces/WorkspaceProvider";
import { useAppExperience } from "../app/AppExperience";

const HISTORY_LIMIT = 100;
type Translate = (key: string, values?: Record<string, string | number>) => string;

export function InboxPage() {
  const { t } = useAppExperience();
  const { token } = useAuth();
  const { selectedWorkspace, selectedWorkspaceSlug } = useWorkspace();
  const queryClient = useQueryClient();
  const [selectedLeadId, setSelectedLeadId] = useState<string | null>(null);
  const [composerValue, setComposerValue] = useState("");
  const [replyResult, setReplyResult] = useState<DirectSalesReply | null>(null);
  const [mobileThreadOpen, setMobileThreadOpen] = useState(false);

  const leadsQuery = useQuery({
    queryKey: queryKeys.leads(selectedWorkspaceSlug ?? "none"),
    queryFn: () => apiClient.leads(token as string, selectedWorkspaceSlug as string),
    enabled: Boolean(token && selectedWorkspaceSlug)
  });

  const leads = useMemo(() => leadsQuery.data ?? [], [leadsQuery.data]);

  useEffect(() => {
    setSelectedLeadId(null);
    setReplyResult(null);
    setComposerValue("");
    setMobileThreadOpen(false);
  }, [selectedWorkspaceSlug]);

  useEffect(() => {
    if (!selectedLeadId && leads.length > 0) {
      setSelectedLeadId(leads[0].id);
    }
    if (selectedLeadId && leads.length > 0 && !leads.some((lead) => lead.id === selectedLeadId)) {
      setSelectedLeadId(leads[0].id);
    }
  }, [leads, selectedLeadId]);

  const selectedLead = leads.find((lead) => lead.id === selectedLeadId) ?? null;

  const conversationQuery = useQuery({
    queryKey: selectedLeadId
      ? queryKeys.conversation(selectedWorkspaceSlug ?? "none", selectedLeadId)
      : ["conversation", "none"],
    queryFn: () =>
      apiClient.conversationHistory(
        token as string,
        selectedWorkspaceSlug as string,
        selectedLeadId as string,
        HISTORY_LIMIT
      ),
    enabled: Boolean(token && selectedWorkspaceSlug && selectedLeadId)
  });

  const replyMutation = useMutation({
    mutationFn: (content: string) => {
      if (!token || !selectedWorkspaceSlug || !selectedLead) {
        throw new Error("Missing selected conversation");
      }
      return apiClient.replyToConversation({
        token,
        workspaceSlug: selectedWorkspaceSlug,
        leadId: selectedLead.id,
        content,
        channel: selectedLead.source || "console",
        idempotencyKey: makeIdempotencyKey()
      });
    },
    onSuccess: async (result) => {
      setReplyResult(result);
      setComposerValue("");
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: queryKeys.conversation(selectedWorkspaceSlug as string, result.lead_id)
        }),
        queryClient.invalidateQueries({ queryKey: queryKeys.leads(selectedWorkspaceSlug as string) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.approvals(selectedWorkspaceSlug as string) })
      ]);
    }
  });

  const selectLead = (leadId: string) => {
    setSelectedLeadId(leadId);
    setReplyResult(null);
    setComposerValue("");
    setMobileThreadOpen(true);
  };

  const onSubmit = () => {
    const content = composerValue.trim();
    if (!content || replyMutation.isPending) {
      return;
    }
    replyMutation.mutate(content);
  };

  return (
    <div className="h-[calc(100vh-4rem)] min-h-[640px] overflow-hidden">
      <PageHeader
        eyebrow={t("inbox")}
        title={t("conversations")}
        description={t("inboxDescription")}
        action={<Badge tone="blue">{selectedWorkspace?.name ?? t("noWorkspace")}</Badge>}
      />

      <div className="grid h-[calc(100%-105px)] grid-cols-1 overflow-hidden lg:grid-cols-[360px_minmax(0,1fr)]">
        <section
          className={`border-r border-slate-200 bg-white ${mobileThreadOpen ? "hidden lg:block" : "block"}`}
          aria-label={t("conversationList")}
        >
          <ConversationList
            leads={leads}
            loading={leadsQuery.isLoading}
            error={leadsQuery.error}
            selectedLeadId={selectedLeadId}
            onSelect={selectLead}
          />
        </section>

        <section
          className={`min-w-0 bg-slate-50 ${mobileThreadOpen || !selectedLead ? "block" : "hidden lg:block"}`}
          aria-label={t("conversationThread")}
        >
          {selectedLead ? (
            <ConversationThread
              lead={selectedLead}
              messages={conversationQuery.data ?? []}
              loading={conversationQuery.isLoading}
              error={conversationQuery.error}
              composerValue={composerValue}
              setComposerValue={setComposerValue}
              onSubmit={onSubmit}
              sending={replyMutation.isPending}
              replyError={replyMutation.error}
              replyResult={replyResult}
              onBack={() => setMobileThreadOpen(false)}
            />
          ) : (
            <div className="flex h-full items-center justify-center p-6">
              <EmptyState
                icon={MessageSquareText}
                title={t("selectConversation")}
                description={t("selectConversationDescription")}
              />
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

function ConversationList({
  leads,
  loading,
  error,
  selectedLeadId,
  onSelect
}: {
  leads: LeadRead[];
  loading: boolean;
  error: Error | null;
  selectedLeadId: string | null;
  onSelect: (leadId: string) => void;
}) {
  const { t } = useAppExperience();
  if (loading) {
    return <LoadingState label={t("loadingConversations")} />;
  }

  if (error) {
    return <div className="p-4"><ErrorState description={t("unableLoadLeads")} /></div>;
  }

  if (leads.length === 0) {
    return (
      <div className="p-4">
        <EmptyState
          icon={UserRound}
          title={t("noConversationsYet")}
          description={t("noConversationsDescription")}
        />
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-slate-200 px-4 py-3">
        <p className="text-sm font-semibold text-slate-950">{t("workspaceConversations", { count: leads.length })}</p>
        <p className="mt-1 text-xs text-slate-500">{t("leadListSorted")}</p>
      </div>
      <div className="flex-1 overflow-y-auto">
        {leads.map((lead) => (
          <button
            key={lead.id}
            type="button"
            onClick={() => onSelect(lead.id)}
            className={`block w-full border-b border-slate-100 px-4 py-4 text-left transition hover:bg-slate-50 ${
              selectedLeadId === lead.id ? "bg-brand-50" : "bg-white"
            }`}
            aria-current={selectedLeadId === lead.id ? "true" : undefined}
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="bidi-data truncate text-sm font-semibold text-slate-950" dir="auto">{lead.full_name}</p>
                <p className="bidi-data mt-1 truncate text-sm text-slate-600" dir="auto">{lead.company_name}</p>
              </div>
              <Badge tone={lead.status === "new" ? "blue" : "slate"}>{t(lead.status) === lead.status ? lead.status : t(lead.status)}</Badge>
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-slate-500">
              <span className="bidi-data rounded-md bg-slate-100 px-2 py-1" dir="auto">{lead.source || t("unknown")}</span>
              <span className="rounded-md bg-slate-100 px-2 py-1">{t(lead.sales_stage) === lead.sales_stage ? lead.sales_stage : t(lead.sales_stage)}</span>
              <span>{formatDate(lead.updated_at)}</span>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}

function ConversationThread({
  lead,
  messages,
  loading,
  error,
  composerValue,
  setComposerValue,
  onSubmit,
  sending,
  replyError,
  replyResult,
  onBack
}: {
  lead: LeadRead;
  messages: ConversationMessageRead[];
  loading: boolean;
  error: Error | null;
  composerValue: string;
  setComposerValue: (value: string) => void;
  onSubmit: () => void;
  sending: boolean;
  replyError: Error | null;
  replyResult: DirectSalesReply | null;
  onBack: () => void;
}) {
  const { t } = useAppExperience();
  return (
    <div className="grid h-full min-w-0 grid-rows-[auto_minmax(0,1fr)_auto]">
      <div className="border-b border-slate-200 bg-white px-4 py-4">
        <div className="flex items-start justify-between gap-3">
          <div className="flex min-w-0 items-start gap-3">
            <Button type="button" variant="ghost" className="px-2 lg:hidden" onClick={onBack} aria-label={t("backToConversations")}>
              <ArrowLeft aria-hidden="true" className="h-5 w-5" />
            </Button>
            <div className="min-w-0">
              <h2 className="bidi-data truncate text-lg font-semibold text-slate-950" dir="auto">{lead.full_name}</h2>
              <p className="bidi-data mt-1 truncate text-sm text-slate-600" dir="auto">{lead.company_name}</p>
            </div>
          </div>
          <Badge tone="slate">{lead.source || "console"}</Badge>
        </div>
        <LeadContext lead={lead} />
      </div>

      <div className="min-h-0 overflow-y-auto px-4 py-5">
        {loading ? <LoadingState label={t("loadingConversationHistory")} /> : null}
        {error ? <ErrorState description={safeConversationError(error, t)} /> : null}
        {!loading && !error && messages.length === 0 ? (
          <EmptyState
            icon={MessageSquareText}
            title={t("noMessagesYet")}
            description={t("noMessagesDescription")}
          />
        ) : null}
        {!loading && !error && messages.length > 0 ? (
          <div className="space-y-4">
            {messages.map((message) => (
              <MessageBubble key={message.id} message={message} />
            ))}
          </div>
        ) : null}
      </div>

      <div className="border-t border-slate-200 bg-white p-4">
        <ReplyStatus result={replyResult} error={replyError} />
        <div className="mt-3">
          <label className="sr-only" htmlFor="sales-reply-composer">{t("replyToCustomer")}</label>
          <textarea
            id="sales-reply-composer"
            value={composerValue}
            onChange={(event) => setComposerValue(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                onSubmit();
              }
            }}
            rows={3}
            placeholder={t("replyPlaceholder")}
            className="min-h-24 w-full resize-none rounded-md border border-slate-300 px-3 py-3 text-sm text-slate-950 shadow-sm placeholder:text-slate-400 focus:border-brand-500"
          />
        </div>
        <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-xs text-slate-500">{t("replyKeyboardHint")}</p>
          <Button
            type="button"
            onClick={onSubmit}
            disabled={sending || composerValue.trim().length === 0}
          >
            <Send aria-hidden="true" className="h-4 w-4" />
            {sending ? t("sending") : t("sendToSales")}
          </Button>
        </div>
      </div>
    </div>
  );
}

function MessageBubble({ message }: { message: ConversationMessageRead }) {
  const { t } = useAppExperience();
  const outbound = message.direction === "outbound";
  return (
    <article className={`flex ${outbound ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[86%] rounded-lg px-4 py-3 shadow-sm ${
          outbound
            ? "bg-slate-950 text-white"
            : "border border-slate-200 bg-white text-slate-950"
        }`}
      >
        <div className={`mb-2 flex flex-wrap items-center gap-2 text-xs ${outbound ? "text-slate-300" : "text-slate-500"}`}>
          <span>{outbound ? t("sales") : t("customer")}</span>
          <span>{message.channel}</span>
          <span>{t(message.stage) === message.stage ? message.stage : t(message.stage)}</span>
          <time dateTime={message.created_at}>{formatDate(message.created_at)}</time>
        </div>
        <p className="bidi-data whitespace-pre-wrap text-sm leading-6" dir="auto">{message.content}</p>
      </div>
    </article>
  );
}

function LeadContext({ lead }: { lead: LeadRead }) {
  const { t } = useAppExperience();
  const rows = [
    { icon: Building2, label: t("company"), value: lead.company_name },
    { icon: Mail, label: t("email"), value: lead.email },
    { icon: Phone, label: t("phone"), value: lead.phone },
    { icon: Clock, label: t("stage"), value: t(lead.sales_stage) === lead.sales_stage ? lead.sales_stage : t(lead.sales_stage) }
  ].filter((row) => row.value);

  return (
    <div className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
      {rows.map((row) => (
        <div key={row.label} className="flex min-w-0 items-center gap-2 rounded-md bg-slate-50 px-3 py-2 text-sm">
          <row.icon aria-hidden="true" className="h-4 w-4 shrink-0 text-slate-400" />
          <span className="bidi-data min-w-0 truncate text-slate-700" dir="auto">{row.value}</span>
        </div>
      ))}
    </div>
  );
}

function ReplyStatus({
  result,
  error
}: {
  result: DirectSalesReply | null;
  error: Error | null;
}) {
  const { t } = useAppExperience();
  if (error) {
    return <ErrorState description={safeReplyError(error, t)} />;
  }
  if (!result) {
    return null;
  }
  if (result.handoff_required) {
    return (
      <Card className="border-amber-200 bg-amber-50 p-3">
        <div className="flex gap-3 text-amber-800">
          <ShieldAlert aria-hidden="true" className="mt-0.5 h-5 w-5 shrink-0" />
          <div>
            <p className="text-sm font-semibold">{t("humanHandoffRequired")}</p>
            <p className="mt-1 text-sm">{t("reason")}: <span className="bidi-data" dir="auto">{result.handoff_reason_code ?? t("backendPolicy")}</span></p>
          </div>
        </div>
      </Card>
    );
  }
  if (result.approval_id) {
    return (
      <Card className="border-brand-100 bg-brand-50 p-3">
        <div className="flex gap-3 text-brand-700">
          <CheckCircle2 aria-hidden="true" className="mt-0.5 h-5 w-5 shrink-0" />
          <div>
            <p className="text-sm font-semibold">{t("approvalRequired")}</p>
            <p className="mt-1 text-sm">
              {t("createdPendingApproval")}
            </p>
            <p className="mt-2 whitespace-pre-wrap text-sm text-slate-700">{result.draft_reply}</p>
          </div>
        </div>
      </Card>
    );
  }
  return (
    <Card className="border-emerald-200 bg-emerald-50 p-3">
      <p className="text-sm font-semibold text-emerald-800">{t("backendSalesReplyGenerated")}</p>
      <p className="mt-2 whitespace-pre-wrap text-sm text-slate-700">{result.draft_reply}</p>
    </Card>
  );
}

function safeConversationError(error: Error, t: Translate): string {
  if (error instanceof ApiError && error.status === 404) {
    return t("conversationUnavailable");
  }
  if (error instanceof ApiError && error.status === 403) {
    return t("conversationForbidden");
  }
  return t("unableLoadConversation");
}

function safeReplyError(error: Error, t: Translate): string {
  if (error instanceof ApiError && error.status === 404) {
    return t("leadUnavailable");
  }
  if (error instanceof ApiError && error.status === 403) {
    return t("replyForbidden");
  }
  if (error instanceof ApiError && error.status === 409) {
    return t("replyConflict");
  }
  if (error instanceof ApiError && error.status === 429) {
    return t("replyRateLimited");
  }
  return t("unableSendReply");
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit"
  }).format(new Date(value));
}

function makeIdempotencyKey(): string {
  return globalThis.crypto?.randomUUID?.() ?? `inbox-${Date.now()}-${Math.random()}`;
}
