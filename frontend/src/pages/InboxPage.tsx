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

const HISTORY_LIMIT = 100;

export function InboxPage() {
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
        eyebrow="Sales Inbox"
        title="Conversations"
        description="Inspect real workspace leads, conversation history, and backend-generated Sales replies."
        action={<Badge tone="blue">{selectedWorkspace?.name ?? "No workspace"}</Badge>}
      />

      <div className="grid h-[calc(100%-105px)] grid-cols-1 overflow-hidden lg:grid-cols-[360px_minmax(0,1fr)]">
        <section
          className={`border-r border-slate-200 bg-white ${mobileThreadOpen ? "hidden lg:block" : "block"}`}
          aria-label="Conversation list"
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
          aria-label="Conversation thread"
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
                title="Select a conversation"
                description="Choose a lead from the inbox to inspect workspace-scoped Sales history."
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
  if (loading) {
    return <LoadingState label="Loading conversations" />;
  }

  if (error) {
    return <div className="p-4"><ErrorState description="Unable to load workspace leads." /></div>;
  }

  if (leads.length === 0) {
    return (
      <div className="p-4">
        <EmptyState
          icon={UserRound}
          title="No conversations yet"
          description="Leads created in this workspace will appear here as the Sales inbox list."
        />
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-slate-200 px-4 py-3">
        <p className="text-sm font-semibold text-slate-950">{leads.length} workspace conversations</p>
        <p className="mt-1 text-xs text-slate-500">Lead list sorted by backend creation time.</p>
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
                <p className="truncate text-sm font-semibold text-slate-950">{lead.full_name}</p>
                <p className="mt-1 truncate text-sm text-slate-600">{lead.company_name}</p>
              </div>
              <Badge tone={lead.status === "new" ? "blue" : "slate"}>{lead.status}</Badge>
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-slate-500">
              <span className="rounded-md bg-slate-100 px-2 py-1">{lead.source || "unknown"}</span>
              <span className="rounded-md bg-slate-100 px-2 py-1">{lead.sales_stage}</span>
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
  return (
    <div className="grid h-full min-w-0 grid-rows-[auto_minmax(0,1fr)_auto]">
      <div className="border-b border-slate-200 bg-white px-4 py-4">
        <div className="flex items-start justify-between gap-3">
          <div className="flex min-w-0 items-start gap-3">
            <Button type="button" variant="ghost" className="px-2 lg:hidden" onClick={onBack} aria-label="Back to conversations">
              <ArrowLeft aria-hidden="true" className="h-5 w-5" />
            </Button>
            <div className="min-w-0">
              <h2 className="truncate text-lg font-semibold text-slate-950">{lead.full_name}</h2>
              <p className="mt-1 truncate text-sm text-slate-600">{lead.company_name}</p>
            </div>
          </div>
          <Badge tone="slate">{lead.source || "console"}</Badge>
        </div>
        <LeadContext lead={lead} />
      </div>

      <div className="min-h-0 overflow-y-auto px-4 py-5">
        {loading ? <LoadingState label="Loading conversation history" /> : null}
        {error ? <ErrorState description={safeConversationError(error)} /> : null}
        {!loading && !error && messages.length === 0 ? (
          <EmptyState
            icon={MessageSquareText}
            title="No messages yet"
            description="The backend has no persisted conversation messages for this lead yet."
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
          <label className="sr-only" htmlFor="sales-reply-composer">Reply to customer</label>
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
            placeholder="Write the customer message for the backend Sales engine..."
            className="min-h-24 w-full resize-none rounded-md border border-slate-300 px-3 py-3 text-sm text-slate-950 shadow-sm placeholder:text-slate-400 focus:border-brand-500"
          />
        </div>
        <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-xs text-slate-500">Enter sends. Shift+Enter keeps a new line.</p>
          <Button
            type="button"
            onClick={onSubmit}
            disabled={sending || composerValue.trim().length === 0}
          >
            <Send aria-hidden="true" className="h-4 w-4" />
            {sending ? "Sending" : "Send to Sales"}
          </Button>
        </div>
      </div>
    </div>
  );
}

function MessageBubble({ message }: { message: ConversationMessageRead }) {
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
          <span>{outbound ? "Sales" : "Customer"}</span>
          <span>{message.channel}</span>
          <span>{message.stage}</span>
          <time dateTime={message.created_at}>{formatDate(message.created_at)}</time>
        </div>
        <p className="whitespace-pre-wrap text-sm leading-6">{message.content}</p>
      </div>
    </article>
  );
}

function LeadContext({ lead }: { lead: LeadRead }) {
  const rows = [
    { icon: Building2, label: "Company", value: lead.company_name },
    { icon: Mail, label: "Email", value: lead.email },
    { icon: Phone, label: "Phone", value: lead.phone },
    { icon: Clock, label: "Stage", value: lead.sales_stage }
  ].filter((row) => row.value);

  return (
    <div className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
      {rows.map((row) => (
        <div key={row.label} className="flex min-w-0 items-center gap-2 rounded-md bg-slate-50 px-3 py-2 text-sm">
          <row.icon aria-hidden="true" className="h-4 w-4 shrink-0 text-slate-400" />
          <span className="min-w-0 truncate text-slate-700">{row.value}</span>
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
  if (error) {
    return <ErrorState description={safeReplyError(error)} />;
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
            <p className="text-sm font-semibold">Human handoff required</p>
            <p className="mt-1 text-sm">Reason: {result.handoff_reason_code ?? "backend policy"}</p>
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
            <p className="text-sm font-semibold">Approval required</p>
            <p className="mt-1 text-sm">
              The backend created a pending human approval. No external delivery is implied here.
            </p>
            <p className="mt-2 whitespace-pre-wrap text-sm text-slate-700">{result.draft_reply}</p>
          </div>
        </div>
      </Card>
    );
  }
  return (
    <Card className="border-emerald-200 bg-emerald-50 p-3">
      <p className="text-sm font-semibold text-emerald-800">Backend Sales reply generated</p>
      <p className="mt-2 whitespace-pre-wrap text-sm text-slate-700">{result.draft_reply}</p>
    </Card>
  );
}

function safeConversationError(error: Error): string {
  if (error instanceof ApiError && error.status === 404) {
    return "This conversation is unavailable in the selected workspace.";
  }
  if (error instanceof ApiError && error.status === 403) {
    return "You do not have permission to operate this conversation.";
  }
  return "Unable to load this conversation.";
}

function safeReplyError(error: Error): string {
  if (error instanceof ApiError && error.status === 404) {
    return "This lead is no longer available in the selected workspace.";
  }
  if (error instanceof ApiError && error.status === 403) {
    return "You do not have permission to reply in this workspace.";
  }
  if (error instanceof ApiError && error.status === 409) {
    return "The backend rejected this reply because of a conversation state conflict.";
  }
  if (error instanceof ApiError && error.status === 429) {
    return "Sales reply generation is currently rate limited. Try again shortly.";
  }
  return "Unable to send this message to the backend Sales engine.";
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
