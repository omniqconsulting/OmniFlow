import { apiRequest, apiUpload } from "./client";

export type ChecklistFreqType =
  | "DAILY" | "WEEKLY" | "MONTHLY" | "QUARTERLY" | "YEARLY"
  | "WEEKLY_CUSTOM" | "MONTHLY_DATE" | "YEARLY_DATE"
  | "NTH_WEEKDAY_MONTH" | "NTH_WEEKDAY_QUARTER";

export type ChecklistStatus = "PENDING" | "IN_PROGRESS" | "OVERDUE" | "DONE" | "FAILED";

export type ChecklistItem = {
  template_id: string;
  assignment_id: string | null;
  title: string;
  description: string;
  frequency_type: ChecklistFreqType | null;
  frequency_label: string;
  evidence_required: boolean;
  is_active: boolean;
  status: ChecklistStatus | null;
  due_at: string | null;
  completed_at: string | null;
  failure_note: string | null;
  delay_reason: string | null;
  employee_id: string | null;
  employee_name: string | null;
  department_name: string | null;
  branch_name: string | null;
  manager_name: string | null;
  compliance_pct: number;
};

export function listChecklists(): Promise<ChecklistItem[]> {
  return apiRequest<ChecklistItem[]>("/api/v1/checklists");
}

export function getChecklistTemplate(templateId: string, employeeId?: string): Promise<ChecklistItem> {
  const q = employeeId ? `?employee_id=${encodeURIComponent(employeeId)}` : "";
  return apiRequest<ChecklistItem>(`/api/v1/checklists/templates/${templateId}${q}`);
}

export type ChecklistHistoryRecord = { date: string | null; status: ChecklistStatus; note: string | null };
export type ChecklistHistory = {
  title: string;
  frequency_label: string;
  done_count: number;
  failed_count: number;
  compliance_pct: number;
  records: ChecklistHistoryRecord[];
};

export function getChecklistHistory(templateId: string, employeeId?: string): Promise<ChecklistHistory> {
  const q = employeeId ? `?employee_id=${encodeURIComponent(employeeId)}` : "";
  return apiRequest<ChecklistHistory>(`/api/v1/checklists/templates/${templateId}/history${q}`);
}

export type ChecklistFormInput = {
  title: string;
  description?: string;
  frequency_type: ChecklistFreqType;
  dow_days?: number[];
  dom_day?: number;
  doy_month?: number;
  doy_day?: number;
  nth?: number;
  nth_weekday?: number;
  is_recurring?: boolean;
  due_time_mode?: "ANYTIME" | "FIXED_TIME";
  due_time?: string;
  evidence_required?: boolean;
  assigned_to_user_id?: string;
  assigned_to_dept_id?: string;
  assigned_to_role?: "EMPLOYEE" | "MANAGER" | "ADMIN";
};

export function createChecklistTemplate(input: ChecklistFormInput): Promise<ChecklistItem> {
  return apiRequest<ChecklistItem>("/api/v1/checklists/templates", { method: "POST", body: input });
}

export function updateChecklistTemplate(templateId: string, input: ChecklistFormInput): Promise<ChecklistItem> {
  return apiRequest<ChecklistItem>(`/api/v1/checklists/templates/${templateId}`, { method: "PUT", body: input });
}

export function deleteChecklistTemplate(templateId: string): Promise<void> {
  return apiRequest<void>(`/api/v1/checklists/templates/${templateId}`, { method: "DELETE" });
}

export function completeChecklist(assignmentId: string, note = ""): Promise<ChecklistItem> {
  return apiRequest<ChecklistItem>(`/api/v1/checklists/assignments/${assignmentId}/complete`, {
    method: "POST",
    body: { note },
  });
}

export function failChecklist(assignmentId: string, note = ""): Promise<ChecklistItem> {
  return apiRequest<ChecklistItem>(`/api/v1/checklists/assignments/${assignmentId}/fail`, {
    method: "POST",
    body: { note },
  });
}

export function notifyChecklist(assignmentId: string): Promise<{ notified: boolean }> {
  return apiRequest<{ notified: boolean }>(`/api/v1/checklists/assignments/${assignmentId}/notify`, { method: "POST" });
}

// Reuses the same evidence-upload endpoint the My Tasks checklist flow
// already calls — a checklist assignment's evidence is a single shared
// upload path regardless of which screen triggers it.
export function uploadChecklistEvidence(assignmentId: string, fileUri: string, fileName = "evidence.jpg") {
  const form = new FormData();
  form.append("evidence_file", { uri: fileUri, name: fileName, type: "image/jpeg" } as unknown as Blob);
  return apiUpload<{ file_name: string; file_path: string; file_type: string; file_size: number }>(
    `/api/v1/tasks/${assignmentId}/evidence`,
    form
  );
}

export type FilterOptions = {
  branches: { id: string; name: string }[];
  departments: { id: string; name: string; branch_id: string | null }[];
  managers: { id: string; name: string }[];
  employees: { id: string; name: string; department_id: string | null; branch_id: string | null; manager_id: string | null; role: string }[];
};

export function getChecklistFilterOptions(): Promise<FilterOptions> {
  return apiRequest<FilterOptions>("/api/v1/checklists/filter-options");
}
