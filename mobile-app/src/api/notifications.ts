import { apiRequest } from "./client";

export type NotificationItem = {
  id: string;
  icon: string;
  cat: string;
  title: string;
  body: string | null;
  meta: string;
  rel: string;
  day: "today" | "earlier";
  is_read: boolean;
  link_type: "ticket" | "none";
  link_id: string | null;
};

type Page<T> = { items: T[]; next_cursor: string | null };

export function listNotifications(cursor?: string, limit = 30): Promise<Page<NotificationItem>> {
  const q = new URLSearchParams();
  q.set("limit", String(limit));
  if (cursor) q.set("cursor", cursor);
  return apiRequest<Page<NotificationItem>>(`/api/v1/notifications?${q.toString()}`);
}

export function getUnreadCount(): Promise<{ unread: number }> {
  return apiRequest("/api/v1/notifications/unread-count");
}

export function markNotificationRead(id: string): Promise<NotificationItem> {
  return apiRequest(`/api/v1/notifications/${id}/read`, { method: "POST" });
}

export function markAllNotificationsRead(): Promise<void> {
  return apiRequest("/api/v1/notifications/read-all", { method: "POST" });
}

export function deleteNotification(id: string): Promise<void> {
  return apiRequest(`/api/v1/notifications/${id}`, { method: "DELETE" });
}
