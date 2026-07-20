import { apiRequest } from "./client";

export type HomeSummary = {
  role: string;
  open_tickets: number;
  open_checklists: number;
  unread_notifications: number;
};

export function getHomeSummary(): Promise<HomeSummary> {
  return apiRequest<HomeSummary>("/api/v1/home");
}
