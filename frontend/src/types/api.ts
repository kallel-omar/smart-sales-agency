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

export type WorkItemStatus =
  | "created"
  | "assigned"
  | "running"
  | "waiting"
  | "approval_required"
  | "completed"
  | "failed"
  | "cancelled"
  | "expired";

export interface OperatorToolAccessRead {
  integration_account_id: string;
  provider: string;
  external_account_id: string | null;
  action_type: string;
  autonomy_level: string;
  active: boolean;
}

export interface OperatorCapabilityRead {
  id: string;
  assignment_id: string;
  key: string;
  active: boolean;
  tool_access: OperatorToolAccessRead[];
}

export interface OperatorAIEmployeeRead {
  id: string;
  name: string;
  role_key: string;
  active: boolean;
  department_id: string;
  department: string;
  capabilities: OperatorCapabilityRead[];
  created_at: string;
  updated_at: string;
}

export interface OperatorWorkItemRead {
  id: string;
  title: string;
  work_type: string;
  status: WorkItemStatus;
  department_id: string;
  department: string;
  ai_employee_id: string | null;
  ai_employee_name: string | null;
  capability_id: string | null;
  capability_key: string | null;
  correlation_id: string;
  input: Record<string, unknown>;
  result: Record<string, unknown> | null;
  error_code: string | null;
  error_message: string | null;
  source_follow_up_task_id: string | null;
  parent_work_item_id: string | null;
  approval_id: string | null;
  approval_status: string | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  completed_at: string | null;
  expires_at: string | null;
}

export interface OperatorApprovalRead {
  id: string;
  status: "pending" | "approved" | "rejected" | "executed";
  action_type: string;
  channel: string;
  payload: Record<string, unknown>;
  reviewer_note: string | null;
  created_at: string;
  decided_at: string | null;
  lead_id: string | null;
  lead_name: string | null;
  company_name: string | null;
  work_item_id: string | null;
  work_item_title: string | null;
  work_type: string | null;
  work_item_status: WorkItemStatus | null;
  ai_employee_name: string | null;
  capability_key: string | null;
  integration_provider: string | null;
  integration_external_account_id: string | null;
}

export type AnalyticsDays = 7 | 30 | 90;

export interface OperatorAnalyticsWorkBreakdownRead {
  key: string;
  total: number;
  completed: number;
  failed: number;
  success_rate: number | null;
}

export interface OperatorAnalyticsUsageBreakdownRead {
  key: string;
  invocation_count: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  known_estimated_cost: string;
  unknown_pricing_invocation_count: number;
}

export interface OperatorAnalyticsRead {
  period: { days: AnalyticsDays; starts_at: string; ends_at: string };
  workitems: {
    current: Record<WorkItemStatus, number>;
    created: number;
    completed: number;
    failed: number;
    success_rate: number | null;
    average_completion_seconds: number | null;
    by_work_type: OperatorAnalyticsWorkBreakdownRead[];
  };
  workforce: Array<{
    employee_id: string;
    name: string;
    role: string;
    department: string;
    workitems: number;
    completed: number;
    failed: number;
    success_rate: number | null;
    invocation_count: number;
    input_tokens: number;
    output_tokens: number;
    total_tokens: number;
    known_estimated_cost: string;
    unknown_pricing_invocation_count: number;
  }>;
  capabilities: Array<{
    capability_id: string;
    key: string;
    workitems: number;
    completed: number;
    failed: number;
    success_rate: number | null;
    invocation_count: number;
    total_tokens: number;
    known_estimated_cost: string;
    unknown_pricing_invocation_count: number;
  }>;
  approvals: {
    requests_created: number;
    pending: number;
    approved: number;
    rejected: number;
    workitems_with_approval_request: number;
    approval_request_rate: number | null;
  };
  ai_usage: {
    invocation_count: number;
    input_tokens: number;
    output_tokens: number;
    total_tokens: number;
    known_estimated_cost: string;
    unknown_pricing_invocation_count: number;
    by_provider: OperatorAnalyticsUsageBreakdownRead[];
    by_model: OperatorAnalyticsUsageBreakdownRead[];
  };
  sales: {
    total_leads: number;
    leads_created: number;
    won_leads: number;
    by_status: Record<string, number>;
    outcomes: {
      capture_lead_completed: number;
      qualification_completed: number;
      follow_up_completed: number;
    };
  };
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
