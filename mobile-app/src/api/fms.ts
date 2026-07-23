import { apiRequest, apiUpload } from "./client";

export type FMSCustomField = { id: string; field_type: string; label: string; required: boolean };
export type FMSStage = {
  id: string;
  name: string;
  order: number;
  color: string;
  target_tat_hours: number | null;
  is_terminal: boolean;
  evidence_required: boolean;
  completion_note_required: boolean;
  custom_fields: FMSCustomField[];
  has_linked_flow: boolean;
};
export type FMSFlow = { id: string; name: string; color: string; stages: FMSStage[]; has_next_flow: boolean };

export type FMSEmployee = { id: string; name: string };

export type FMSTicketStatus = "ACTIVE" | "STAGE_COMPLETE" | "IN_TRANSITION" | "HELP_REQUESTED" | "FLAGGED" | "ON_HOLD" | "COMPLETED" | "CLOSED";

export type FMSTicket = {
  id: string;
  display_id: string | null;
  flow_id: string;
  flow_name: string;
  current_stage_id: string | null;
  current_stage_name: string | null;
  current_stage_order: number | null;
  title: string;
  status: FMSTicketStatus;
  priority: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
  current_assignee_id: string | null;
  assignee_name: string | null;
  due_at: string | null;
  is_flagged: boolean;
  flagged_reason: string | null;
  created_at: string;
  tat_pct: number | null;
  pause_reason: string | null;
  continued_from_ticket_id: string | null;
  continued_from_display_id: string | null;
  continued_to_ticket_id: string | null;
  continued_to_display_id: string | null;
  linked_child_ticket_id: string | null;
  linked_child_display_id: string | null;
  linked_parent_ticket_id: string | null;
  linked_parent_display_id: string | null;
};

export type FMSStageHistoryEntry = {
  stage_id: string;
  stage_name: string;
  entered_at: string;
  exited_at: string | null;
  assignee_id: string | null;
  assignee_name: string | null;
};

export type FMSTicketDetail = FMSTicket & { stage_history: FMSStageHistoryEntry[] };

export type FMSBoardKpis = { active_tickets: number; tat_breaches: number; flagged: number; awaiting_action: number; compliance_pct: number };
export type FMSBoard = { kpis: FMSBoardKpis; tickets: FMSTicket[] };

export type FMSTicketEvent = { event_type: string; detail: string; actor_name: string; created_at: string };

export function getFlows(): Promise<FMSFlow[]> {
  return apiRequest<FMSFlow[]>("/api/v1/fms/flows");
}

export function getAssignableEmployees(): Promise<FMSEmployee[]> {
  return apiRequest<FMSEmployee[]>("/api/v1/fms/employees");
}

export function getBoard(params: { flowId?: string; myWork?: boolean }): Promise<FMSBoard> {
  const q = new URLSearchParams();
  if (params.flowId) q.set("flow_id", params.flowId);
  q.set("my_work", params.myWork ? "1" : "0");
  return apiRequest<FMSBoard>(`/api/v1/fms/board?${q.toString()}`);
}

export function getTicket(ticketId: string): Promise<FMSTicketDetail> {
  return apiRequest<FMSTicketDetail>(`/api/v1/fms/tickets/${ticketId}`);
}

export function getTicketEvents(ticketId: string): Promise<FMSTicketEvent[]> {
  return apiRequest<FMSTicketEvent[]>(`/api/v1/fms/tickets/${ticketId}/events`);
}

export function createTicket(input: {
  flowId: string;
  startingStageId: string;
  title: string;
  priority: string;
  assigneeId: string;
  dueAt?: string;
}): Promise<FMSTicket> {
  return apiRequest<FMSTicket>("/api/v1/fms/tickets", {
    method: "POST",
    body: {
      flow_id: input.flowId,
      starting_stage_id: input.startingStageId,
      title: input.title,
      priority: input.priority,
      assignee_id: input.assigneeId,
      due_at: input.dueAt,
    },
  });
}

export function transitionTicket(
  ticketId: string,
  input: {
    nextStageId?: string;
    newAssigneeId?: string;
    completionNote?: string;
    returnReason?: string;
    isOverride?: boolean;
    customFieldValues?: Record<string, string>;
    evidenceUri?: string;
  }
): Promise<FMSTicketDetail> {
  const form = new FormData();
  if (input.nextStageId) form.append("next_stage_id", input.nextStageId);
  if (input.newAssigneeId) form.append("new_assignee_id", input.newAssigneeId);
  form.append("completion_note", input.completionNote ?? "");
  form.append("return_reason", input.returnReason ?? "");
  form.append("is_override", input.isOverride ? "true" : "false");
  form.append("custom_field_values_json", JSON.stringify(input.customFieldValues ?? {}));
  if (input.evidenceUri) {
    form.append("evidence_file", { uri: input.evidenceUri, name: "evidence.jpg", type: "image/jpeg" } as unknown as Blob);
  }
  return apiUpload<FMSTicketDetail>(`/api/v1/fms/tickets/${ticketId}/transition`, form);
}

export type FMSSubAction = "comment" | "flag" | "unflag" | "reassign" | "on_hold" | "resume" | "close" | "add_helper" | "send_to_linked_flow" | "close_and_continue";

export function ticketAction(
  ticketId: string,
  body: { action: FMSSubAction; comment?: string; reason?: string; newAssigneeId?: string; helperId?: string; flagReason?: string }
): Promise<FMSTicketDetail> {
  return apiRequest<FMSTicketDetail>(`/api/v1/fms/tickets/${ticketId}/action`, {
    method: "POST",
    body: {
      action: body.action,
      comment: body.comment ?? "",
      reason: body.reason ?? "",
      new_assignee_id: body.newAssigneeId ?? "",
      helper_id: body.helperId ?? "",
      flag_reason: body.flagReason ?? "",
    },
  });
}

export function requestHelp(ticketId: string, reason: string, helperId?: string): Promise<FMSTicketDetail> {
  return apiRequest<FMSTicketDetail>(`/api/v1/fms/tickets/${ticketId}/help_request`, {
    method: "POST",
    body: { reason, helper_id: helperId },
  });
}

export function bulkTransition(ticketIds: string[], nextStageId: string): Promise<{ moved: number; skipped: { ticket_id: string; reason: string }[] }> {
  return apiRequest(`/api/v1/fms/tickets/bulk-transition`, {
    method: "POST",
    body: { ticket_ids: ticketIds, next_stage_id: nextStageId },
  });
}
