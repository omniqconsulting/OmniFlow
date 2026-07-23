import { apiRequest, apiUpload } from "./client";

export type TicketPriority = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
export type TicketStatus = "OPEN" | "DONE" | "CLOSED";
export type TicketCategory = "NORMAL" | "HELP";

export type Ticket = {
  id: string;
  display_id: string | null;
  title: string;
  description: string;
  priority: TicketPriority;
  status: TicketStatus;
  ticket_type: string;
  ticket_category: TicketCategory;
  created_by_id: string;
  created_by_name: string | null;
  current_assignee_id: string | null;
  assignee_name: string | null;
  due_at: string | null;
  acknowledged_at: string | null;
  closed_at: string | null;
  is_flagged: boolean;
  flagged_reason: string | null;
  evidence_required: boolean;
  created_at: string;
};

export type TicketComment = {
  id: string;
  ticket_id: string;
  user_id: string;
  user_name: string | null;
  body: string;
  created_at: string;
};

export type TicketEvent = {
  id: string;
  ticket_id: string;
  actor_id: string;
  actor_name: string | null;
  event_type: string;
  detail: string | null;
  created_at: string;
};

type Page<T> = { items: T[]; next_cursor: string | null };

export type TicketFilters = {
  status?: TicketStatus | "ACKNOWLEDGED";
  priority?: TicketPriority[];
  ticketCategory?: TicketCategory[];
  assigneeId?: string[];
  dateFrom?: string; // YYYY-MM-DD
  dateTo?: string; // YYYY-MM-DD
  limit?: number;
};

export function listTickets(filters: TicketFilters = {}): Promise<Page<Ticket>> {
  const q = new URLSearchParams();
  q.set("limit", String(filters.limit ?? 50));
  if (filters.status) q.set("status", filters.status);
  (filters.priority ?? []).forEach((p) => q.append("priority", p));
  (filters.ticketCategory ?? []).forEach((c) => q.append("ticket_category", c));
  (filters.assigneeId ?? []).forEach((a) => q.append("assignee_id", a));
  if (filters.dateFrom) q.set("date_from", filters.dateFrom);
  if (filters.dateTo) q.set("date_to", filters.dateTo);
  return apiRequest<Page<Ticket>>(`/api/v1/tickets?${q.toString()}`);
}

export function getTicket(id: string): Promise<Ticket> {
  return apiRequest<Ticket>(`/api/v1/tickets/${id}`);
}

export type LinkedEntitySelectionIn = {
  entityType: string;
  entityId?: string;
  entityLabel: string;
  customText?: string;
};

export function createTicket(params: {
  title: string;
  description: string;
  priority: TicketPriority;
  assigneeId: string;
  dueAt: string;
  evidenceRequired?: boolean;
  ticketCategory?: TicketCategory;
  linkedEntities?: LinkedEntitySelectionIn[];
}): Promise<Ticket> {
  return apiRequest<Ticket>("/api/v1/tickets", {
    method: "POST",
    body: {
      title: params.title,
      description: params.description,
      priority: params.priority,
      assignee_id: params.assigneeId,
      due_at: params.dueAt,
      evidence_required: params.evidenceRequired ?? false,
      ticket_category: params.ticketCategory ?? "NORMAL",
      linked_entities: (params.linkedEntities ?? []).map((l) => ({
        entity_type: l.entityType,
        entity_id: l.entityId,
        entity_label: l.entityLabel,
        custom_text: l.customText,
      })),
    },
  });
}

export function updateTicket(id: string, patch: {
  status?: TicketStatus;
  priority?: TicketPriority;
  title?: string;
  description?: string;
  dueAt?: string;
  assigneeId?: string;
}): Promise<Ticket> {
  return apiRequest<Ticket>(`/api/v1/tickets/${id}`, {
    method: "PATCH",
    body: {
      status: patch.status,
      priority: patch.priority,
      title: patch.title,
      description: patch.description,
      due_at: patch.dueAt,
      assignee_id: patch.assigneeId,
    },
  });
}

export function listComments(ticketId: string): Promise<TicketComment[]> {
  return apiRequest<TicketComment[]>(`/api/v1/tickets/${ticketId}/comments`);
}

export function postComment(ticketId: string, body: string): Promise<TicketComment> {
  return apiRequest<TicketComment>(`/api/v1/tickets/${ticketId}/comments`, {
    method: "POST",
    body: { body },
  });
}

export function listEvents(ticketId: string): Promise<TicketEvent[]> {
  return apiRequest<TicketEvent[]>(`/api/v1/tickets/${ticketId}/events`);
}

export function acknowledgeTicket(ticketId: string): Promise<Ticket> {
  return apiRequest<Ticket>(`/api/v1/tickets/${ticketId}/acknowledge`, { method: "POST" });
}

export function flagTicket(ticketId: string, reason: string): Promise<Ticket> {
  return apiRequest<Ticket>(`/api/v1/tickets/${ticketId}/flag`, { method: "POST", body: { reason } });
}

export function unflagTicket(ticketId: string): Promise<Ticket> {
  return apiRequest<Ticket>(`/api/v1/tickets/${ticketId}/unflag`, { method: "POST" });
}

export function logDelay(ticketId: string, reason: string): Promise<Ticket> {
  return apiRequest<Ticket>(`/api/v1/tickets/${ticketId}/log-delay`, { method: "POST", body: { reason } });
}

export function requestHelp(ticketId: string, reason: string): Promise<Ticket> {
  return apiRequest<Ticket>(`/api/v1/tickets/${ticketId}/request-help`, { method: "POST", body: { reason } });
}

export function deleteTicket(ticketId: string): Promise<void> {
  return apiRequest<void>(`/api/v1/tickets/${ticketId}`, { method: "DELETE" });
}

export type TicketAttachment = {
  id: string;
  file_name: string;
  file_path: string;
  file_type: string | null;
  file_size: number | null;
  uploaded_by_id: string;
  created_at: string;
};

export function listAttachments(ticketId: string): Promise<TicketAttachment[]> {
  return apiRequest<TicketAttachment[]>(`/api/v1/tickets/${ticketId}/attachments`);
}

export function uploadAttachment(ticketId: string, fileUri: string, fileName = "attachment.jpg"): Promise<{ file_name: string }> {
  const form = new FormData();
  form.append("file", { uri: fileUri, name: fileName, type: "image/jpeg" } as unknown as Blob);
  return apiUpload<{ file_name: string }>(`/api/v1/tickets/${ticketId}/attachments`, form);
}

// Evidence submission is the same endpoint as a regular attachment upload —
// kept as a separate name so the "Submit with Evidence" flow reads clearly.
export const uploadEvidence = uploadAttachment;

export type LinkedEntityOptionItem = { id: string; label: string; detail: string };
export type LinkedEntityOption = { key: string; label: string; items: LinkedEntityOptionItem[] };

export function getLinkedEntityOptions(): Promise<LinkedEntityOption[]> {
  return apiRequest<LinkedEntityOption[]>("/api/v1/tickets/linked-entity-options");
}

export type LinkedEntity = {
  id: string;
  entity_type: string;
  entity_id: string | null;
  entity_label: string | null;
  custom_text: string | null;
  created_at: string;
};

export function listLinkedEntities(ticketId: string): Promise<LinkedEntity[]> {
  return apiRequest<LinkedEntity[]>(`/api/v1/tickets/${ticketId}/linked-entities`);
}

export function addLinkedEntity(ticketId: string, selection: LinkedEntitySelectionIn): Promise<LinkedEntity> {
  return apiRequest<LinkedEntity>(`/api/v1/tickets/${ticketId}/linked-entities`, {
    method: "POST",
    body: {
      entity_type: selection.entityType,
      entity_id: selection.entityId,
      entity_label: selection.entityLabel,
      custom_text: selection.customText,
    },
  });
}

export type EmployeeOption = { id: string; name: string };

export function listEmployeeOptions(): Promise<EmployeeOption[]> {
  return apiRequest<Page<{ id: string; name: string }>>("/api/v1/employees?limit=100").then((p) =>
    p.items.map((e) => ({ id: e.id, name: e.name }))
  );
}

export type TicketHelper = {
  id: string;
  user_id: string;
  user_name: string | null;
  note: string | null;
  created_at: string;
};

export function listHelpers(ticketId: string): Promise<TicketHelper[]> {
  return apiRequest<TicketHelper[]>(`/api/v1/tickets/${ticketId}/helpers`);
}

export function addHelper(ticketId: string, userId: string, note = ""): Promise<TicketHelper> {
  return apiRequest<TicketHelper>(`/api/v1/tickets/${ticketId}/helpers`, {
    method: "POST",
    body: { user_id: userId, note },
  });
}

export function removeHelper(ticketId: string, userId: string): Promise<void> {
  return apiRequest<void>(`/api/v1/tickets/${ticketId}/helpers/${userId}`, { method: "DELETE" });
}
