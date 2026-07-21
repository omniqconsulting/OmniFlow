import { apiRequest } from "./client";

export type HomeSummary = {
  role: string;
  open_tickets: number;
  open_checklists: number;
  unread_notifications: number;
  enabled_tabs: string[];
  attendance_today: { checked_in: boolean; checked_out: boolean } | null;
};

export function getHomeSummary(): Promise<HomeSummary> {
  return apiRequest<HomeSummary>("/api/v1/home");
}

export type ActivityItem = {
  icon: string;
  title: string;
  meta: string;
  rel: string;
  cat: string;
};

export function getHomeActivity(): Promise<ActivityItem[]> {
  return apiRequest<ActivityItem[]>("/api/v1/home/activity");
}
