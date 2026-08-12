export interface AccessTokenRead {
  access_token: string;
  token_type: "bearer";
  expires_in: number;
}

export interface UserRead {
  id: string;
  email: string;
  display_name: string | null;
  active: boolean;
  created_at: string;
  updated_at: string;
}

export interface WorkspaceRead {
  id: string;
  slug: string;
  name: string;
  active: boolean;
  created_at: string;
  updated_at: string;
}

export interface LeadRead {
  id: string;
  tenant_id: string;
  full_name: string;
  company_name: string;
  job_title?: string | null;
  email?: string | null;
  phone?: string | null;
  website?: string | null;
  source: string;
  notes?: string | null;
  status: string;
  sales_stage: string;
  score: number;
  created_at: string;
  updated_at: string;
}

export interface ConversationMessageRead {
  id: string;
  lead_id: string;
  direction: "inbound" | "outbound" | string;
  channel: string;
  stage: string;
  content: string;
  created_at: string;
}

export interface DirectSalesReply {
  lead_id: string;
  detected_stage: string;
  draft_reply: string;
  approval_id?: string | null;
  handoff_required: boolean;
  handoff_reason_code?: string | null;
  duplicate?: boolean | null;
}

export interface ApprovalRead {
  id: string;
  lead_id: string | null;
  action_type: string;
  channel: string;
  status: string;
  created_at: string;
  decided_at: string | null;
}

export interface IntegrationAccountRead {
  id: string;
  workspace_id: string;
  provider: string;
  external_account_id: string | null;
  active: boolean;
  created_at: string;
  updated_at: string;
}

export interface IntegrationOperationalSummaryRead {
  active_integration_account_count: number;
  pending_outbound_action_count: number;
  delivered_outbound_action_count: number;
  failed_outbound_action_count: number;
  retryable_failed_action_count: number;
  cancelled_outbound_action_count: number;
  expired_outbound_action_count: number;
  most_recent_outbound_at: string | null;
  recent_delivered_count: number;
  recent_failed_count: number;
  priority_counts: Record<string, number>;
  owned_outbound_action_count: number;
  unowned_outbound_action_count: number;
  archived_outbound_action_count: number;
  unarchived_outbound_action_count: number;
}

export interface AIInvocationUsageSummaryRead {
  invocation_count: number;
  successful_invocation_count: number;
  failed_invocation_count: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  unknown_token_usage_invocation_count: number;
  known_estimated_spend: string;
  unknown_pricing_invocation_count: number;
}
