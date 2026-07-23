import { apiRequest } from "./client";

export { getChecklistFilterOptions as getDashboardFilterOptions, type FilterOptions } from "./checklists";

export type DashboardRange = "today" | "7d" | "30d" | "mtd";

export type PerfComponent = { label: string; value: number; color: string; weight: number };
export type TicketStats = { total: number; open: number; completed: number; on_time_pct: number; on_time_count: number; issues_open: number };
export type ChecklistStats = { due: number; completed: number; compliance_pct: number; on_time: number; missed: number };
export type FmsStats = { total: number; active: number; completed: number; on_time: number; tat_breach: number };
export type PriorityTask = { id: string; title: string; assignee_name: string | null; due_at: string | null; overdue: boolean };
export type DeptHealth = { dept_id: string; name: string; rate: number };

export type DashboardSummary = {
  can_view: boolean;
  score: number;
  components: PerfComponent[];
  tickets: TicketStats | null;
  checklists: ChecklistStats | null;
  fms: FmsStats | null;
  priority_tasks: PriorityTask[];
  priority_tasks_count: number;
  dept_health: DeptHealth[];
};

export function getDashboardSummary(params: {
  range: DashboardRange;
  deptIds?: string[];
  managerIds?: string[];
  branchIds?: string[];
  includeDeptHealth?: boolean;
}): Promise<DashboardSummary> {
  const q = new URLSearchParams();
  q.set("range", params.range);
  (params.deptIds ?? []).forEach((id) => q.append("dept_ids", id));
  (params.managerIds ?? []).forEach((id) => q.append("manager_ids", id));
  (params.branchIds ?? []).forEach((id) => q.append("branch_ids", id));
  q.set("include_dept_health", String(params.includeDeptHealth ?? true));
  return apiRequest<DashboardSummary>(`/api/v1/dashboard/summary?${q.toString()}`);
}
