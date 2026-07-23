import { apiRequest } from "./client";

export type PlanUsage = { label: string; used: number; limit: number | null };
export type SetupRow = { key: string; icon: string; label: string; sub: string };
export type SetupSection = { title: string; rows: SetupRow[] };

export type SetupOverview = {
  tenant_name: string;
  plan: string;
  plan_usage: PlanUsage[];
  sections: SetupSection[];
};

export function getSetupOverview(): Promise<SetupOverview> {
  return apiRequest<SetupOverview>("/api/v1/setup/overview");
}

export type NotificationSettings = {
  work_start_time: string;
  work_end_time: string;
  work_days: number[];
  suppress_notif_outside_hours: boolean;
  ticket_notif_tat_pct: number;
  ticket_notif_tat_pct_both: number;
  checklist_notif_hours: number[];
};

export function getNotificationSettings(): Promise<NotificationSettings> {
  return apiRequest<NotificationSettings>("/api/v1/setup/notifications");
}

export type NotificationSettingsUpdate = {
  suppress_notif_outside_hours: boolean;
};

export function updateNotificationSettings(payload: NotificationSettingsUpdate): Promise<NotificationSettings> {
  return apiRequest<NotificationSettings>("/api/v1/setup/notifications", { method: "PUT", body: payload });
}

// ── Notification rules toggle table ────────────────────────────────────────

export type NotificationCondition = { key: string; category: string; label: string; cadence: string; recipients: string };
export type NotificationRule = { condition_key: string; in_app: boolean; push: boolean; whatsapp: boolean; recipients: string[] };
export type NotificationRulesData = {
  conditions: NotificationCondition[];
  rules: NotificationRule[];
  available_roles: string[];
  role_labels: Record<string, string>;
};

export function getNotificationRules(): Promise<NotificationRulesData> {
  return apiRequest<NotificationRulesData>("/api/v1/setup/notification-rules");
}

export function updateNotificationRules(rules: NotificationRule[]): Promise<NotificationRulesData> {
  return apiRequest<NotificationRulesData>("/api/v1/setup/notification-rules", { method: "PUT", body: rules });
}

// ── Branches ────────────────────────────────────────────────────────────

export type Branch = { id: string; name: string; address: string | null; weekly_off_days: number[] };
export type BranchInput = { name: string; address?: string; weekly_off_days: number[] };

export const branchesApi = {
  list: (): Promise<Branch[]> => apiRequest("/api/v1/setup/branches"),
  create: (body: BranchInput): Promise<Branch> => apiRequest("/api/v1/setup/branches", { method: "POST", body }),
  update: (id: string, body: BranchInput): Promise<Branch> => apiRequest(`/api/v1/setup/branches/${id}`, { method: "PUT", body }),
  remove: (id: string): Promise<void> => apiRequest(`/api/v1/setup/branches/${id}`, { method: "DELETE" }),
};

// ── Departments ─────────────────────────────────────────────────────────

export type Department = { id: string; name: string; branch_id: string | null; branch_name: string | null };
export type DepartmentInput = { name: string; branch_id: string | null };

export const departmentsApi = {
  list: (): Promise<Department[]> => apiRequest("/api/v1/setup/departments"),
  create: (body: DepartmentInput): Promise<Department> => apiRequest("/api/v1/setup/departments", { method: "POST", body }),
  update: (id: string, body: DepartmentInput): Promise<Department> => apiRequest(`/api/v1/setup/departments/${id}`, { method: "PUT", body }),
  remove: (id: string): Promise<void> => apiRequest(`/api/v1/setup/departments/${id}`, { method: "DELETE" }),
};

// ── Customers / Vendors (identical shape) ──────────────────────────────

export type ContactEntity = {
  id: string; name: string; contact_person: string | null; phone: string | null;
  email: string | null; address: string | null; notes: string | null; is_active: boolean;
};
export type ContactEntityInput = {
  name: string; contact_person?: string; phone?: string; email?: string;
  address?: string; notes?: string; is_active: boolean;
};

function contactApi(path: "customers" | "vendors") {
  return {
    list: (): Promise<ContactEntity[]> => apiRequest(`/api/v1/setup/${path}`),
    create: (body: ContactEntityInput): Promise<ContactEntity> => apiRequest(`/api/v1/setup/${path}`, { method: "POST", body }),
    update: (id: string, body: ContactEntityInput): Promise<ContactEntity> => apiRequest(`/api/v1/setup/${path}/${id}`, { method: "PUT", body }),
    remove: (id: string): Promise<void> => apiRequest(`/api/v1/setup/${path}/${id}`, { method: "DELETE" }),
  };
}

export const customersApi = contactApi("customers");
export const vendorsApi = contactApi("vendors");

// ── Raw Materials ───────────────────────────────────────────────────────

export type RawMaterial = { id: string; name: string; unit: string | null; description: string | null; major_supplier: string | null; is_active: boolean };
export type RawMaterialInput = { name: string; unit?: string; description?: string; major_supplier?: string; is_active: boolean };

export const materialsApi = {
  list: (): Promise<RawMaterial[]> => apiRequest("/api/v1/setup/materials"),
  create: (body: RawMaterialInput): Promise<RawMaterial> => apiRequest("/api/v1/setup/materials", { method: "POST", body }),
  update: (id: string, body: RawMaterialInput): Promise<RawMaterial> => apiRequest(`/api/v1/setup/materials/${id}`, { method: "PUT", body }),
  remove: (id: string): Promise<void> => apiRequest(`/api/v1/setup/materials/${id}`, { method: "DELETE" }),
};

// ── End Products ────────────────────────────────────────────────────────

export type EndProduct = { id: string; name: string; sku_code: string | null; unit: string | null; description: string | null; is_active: boolean };
export type EndProductInput = { name: string; sku_code?: string; unit?: string; description?: string; is_active: boolean };

export const productsApi = {
  list: (): Promise<EndProduct[]> => apiRequest("/api/v1/setup/products"),
  create: (body: EndProductInput): Promise<EndProduct> => apiRequest("/api/v1/setup/products", { method: "POST", body }),
  update: (id: string, body: EndProductInput): Promise<EndProduct> => apiRequest(`/api/v1/setup/products/${id}`, { method: "PUT", body }),
  remove: (id: string): Promise<void> => apiRequest(`/api/v1/setup/products/${id}`, { method: "DELETE" }),
};

// ── Units of Measure ────────────────────────────────────────────────────

export type Uom = { id: string; name: string; abbreviation: string; is_active: boolean };
export type UomInput = { name: string; abbreviation: string; is_active: boolean };

export const uomApi = {
  list: (): Promise<Uom[]> => apiRequest("/api/v1/setup/uom"),
  create: (body: UomInput): Promise<Uom> => apiRequest("/api/v1/setup/uom", { method: "POST", body }),
  update: (id: string, body: UomInput): Promise<Uom> => apiRequest(`/api/v1/setup/uom/${id}`, { method: "PUT", body }),
  remove: (id: string): Promise<void> => apiRequest(`/api/v1/setup/uom/${id}`, { method: "DELETE" }),
};

// ── Custom Reference Lists ──────────────────────────────────────────────

export type RefItem = { id: string; value: string; sort_order: number; is_active: boolean };
export type RefList = { id: string; list_name: string; items: RefItem[] };

export const listsApi = {
  list: (): Promise<RefList[]> => apiRequest("/api/v1/setup/lists"),
  create: (list_name: string): Promise<RefList> => apiRequest("/api/v1/setup/lists", { method: "POST", body: { list_name } }),
  remove: (listId: string): Promise<void> => apiRequest(`/api/v1/setup/lists/${listId}`, { method: "DELETE" }),
  addItem: (listId: string, value: string, sort_order = 0): Promise<RefItem> =>
    apiRequest(`/api/v1/setup/lists/${listId}/items`, { method: "POST", body: { value, sort_order } }),
  updateItem: (listId: string, itemId: string, value: string, sort_order = 0): Promise<RefItem> =>
    apiRequest(`/api/v1/setup/lists/${listId}/items/${itemId}`, { method: "PUT", body: { value, sort_order } }),
  removeItem: (listId: string, itemId: string): Promise<void> =>
    apiRequest(`/api/v1/setup/lists/${listId}/items/${itemId}`, { method: "DELETE" }),
};

// ── Performance Formula ─────────────────────────────────────────────────

export type PerfComponent = { key: string; label: string; weight: number };
export type PerfFormula = { label: string | null; components: PerfComponent[] };

export const performanceApi = {
  get: (): Promise<PerfFormula> => apiRequest("/api/v1/setup/performance"),
  save: (label: string | null, weights: Record<string, number>): Promise<PerfFormula> =>
    apiRequest("/api/v1/setup/performance", { method: "PUT", body: { label, weights } }),
};

// ── Day-Status Rules ─────────────────────────────────────────────────────

export type RuleCondition = { field: string; operator: string; value: string };
export type DayStatusRule = {
  id: string; name: string; is_active: boolean; priority: number;
  condition_logic: "ALL" | "ANY"; outcome: "PRESENT" | "HALF_DAY" | "ABSENT"; conditions: RuleCondition[];
};
export type DayStatusRuleInput = {
  name: string; is_active: boolean; priority: number;
  condition_logic: "ALL" | "ANY"; outcome: string; conditions: RuleCondition[];
};
export type FieldCatalogEntry = { field: string; kind: "time" | "numeric" | "boolean"; operators: string[] };

export const dayStatusRulesApi = {
  fields: (): Promise<FieldCatalogEntry[]> => apiRequest("/api/v1/setup/day-status-rules/fields"),
  list: (): Promise<DayStatusRule[]> => apiRequest("/api/v1/setup/day-status-rules"),
  create: (body: DayStatusRuleInput): Promise<DayStatusRule> => apiRequest("/api/v1/setup/day-status-rules", { method: "POST", body }),
  update: (id: string, body: DayStatusRuleInput): Promise<DayStatusRule> => apiRequest(`/api/v1/setup/day-status-rules/${id}`, { method: "PUT", body }),
  remove: (id: string): Promise<void> => apiRequest(`/api/v1/setup/day-status-rules/${id}`, { method: "DELETE" }),
};

// ── FMS Flows (read + active toggle) ────────────────────────────────────

export type Flow = { id: string; name: string; description: string | null; color: string; is_active: boolean; stage_count: number };

export const flowsApi = {
  list: (): Promise<Flow[]> => apiRequest("/api/v1/setup/flows"),
  setActive: (id: string, is_active: boolean): Promise<Flow> =>
    apiRequest(`/api/v1/setup/flows/${id}/active`, { method: "PUT", body: { is_active } }),
};

// ── Employees ────────────────────────────────────────────────────────────

export type EmployeeDetail = {
  id: string; name: string; phone: string; email: string | null; role: string;
  employee_id: string | null; department_id: string | null; branch_id: string | null;
  manager_id: string | null; status: string; joining_date: string | null; created_at: string;
};
export type EmployeeCreateInput = {
  name: string; phone: string; password: string; role: string;
  email?: string; department_id?: string | null; manager_id?: string | null; branch_id?: string | null;
};
export type EmployeeUpdateInput = {
  name: string; phone: string; email?: string; role: string;
  department_id?: string | null; manager_id?: string | null; branch_id?: string | null; joining_date?: string | null;
};

export const employeesApi = {
  list: (opts?: { my_team?: boolean }): Promise<{ items: EmployeeDetail[]; next_cursor: string | null }> =>
    apiRequest(`/api/v1/employees?limit=100${opts?.my_team ? "&my_team=true" : ""}`),
  create: (body: EmployeeCreateInput): Promise<EmployeeDetail> => apiRequest("/api/v1/employees", { method: "POST", body }),
  update: (id: string, body: EmployeeUpdateInput): Promise<EmployeeDetail> => apiRequest(`/api/v1/employees/${id}`, { method: "PUT", body }),
  remove: (id: string): Promise<void> => apiRequest(`/api/v1/employees/${id}`, { method: "DELETE" }),
};
