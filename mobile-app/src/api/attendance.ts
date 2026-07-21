import { apiRequest, apiUpload } from "./client";

export type AttendanceRecord = {
  id: string;
  user_id: string;
  work_date: string; // YYYY-MM-DD
  branch_id: string | null;
  branch_name: string | null;
  check_in_at: string | null;
  check_in_lat: number | null;
  check_in_lng: number | null;
  check_in_in_fence: boolean | null;
  check_in_reason: string | null;
  check_out_at: string | null;
  check_out_lat: number | null;
  check_out_lng: number | null;
  check_out_in_fence: boolean | null;
  check_out_reason: string | null;
  photo_path: string | null;
  is_half_day: boolean;
  recorded_by_name: string | null;
  on_behalf_reason: string | null;
};

export type LeaveRequest = {
  id: string;
  user_id: string;
  leave_type: string;
  start_date: string;
  end_date: string;
  is_half_day: boolean;
  status: string;
  created_at: string;
};

type Page<T> = { items: T[]; next_cursor: string | null };

export function listAttendanceRecords(params: { year?: number; month?: number; limit?: number } = {}): Promise<Page<AttendanceRecord>> {
  const q = new URLSearchParams();
  q.set("limit", String(params.limit ?? 100));
  if (params.year) q.set("year", String(params.year));
  if (params.month) q.set("month", String(params.month));
  return apiRequest<Page<AttendanceRecord>>(`/api/v1/attendance/records?${q.toString()}`);
}

export function listLeaveRequests(limit = 25): Promise<Page<LeaveRequest>> {
  return apiRequest<Page<LeaveRequest>>(`/api/v1/attendance/leave?limit=${limit}`);
}

export type TodayRecord = {
  check_in_at: string | null;
  check_out_at: string | null;
  check_in_in_fence: boolean | null;
  check_out_in_fence: boolean | null;
  checkin_distance_m: number | null;
  checkout_distance_m: number | null;
  branch_name: string | null;
};

export type PunchStatus = {
  has_fence: boolean;
  fence_lat: number | null;
  fence_lng: number | null;
  fence_radius_m: number | null;
  record: TodayRecord | null;
};

export function getPunchStatus(onBehalfOfUserId?: string): Promise<PunchStatus> {
  const qs = onBehalfOfUserId ? `?on_behalf_of_user_id=${encodeURIComponent(onBehalfOfUserId)}` : "";
  return apiRequest<PunchStatus>(`/api/v1/attendance/punch-status${qs}`);
}

export type CheckInResult = {
  ok: boolean;
  check_in_at: string;
  in_fence: boolean;
  branch_name: string | null;
  recorded_for_name: string | null;
};
export type CheckOutResult = { ok: boolean; check_out_at: string; in_fence: boolean; recorded_for_name: string | null };

// Branch is auto-detected server-side from the geofence the coordinates
// fall inside — there's nothing for the employee to pick (see
// _detect_branch_by_geofence in api_v1/attendance.py). onBehalfOf lets an
// admin/manager record for another employee (requires onBehalfReason).
export function checkIn(params: {
  lat: number;
  lng: number;
  reason?: string;
  photoUri: string;
  onBehalfOfUserId?: string;
  onBehalfReason?: string;
}): Promise<CheckInResult> {
  const form = new FormData();
  form.append("lat", String(params.lat));
  form.append("lng", String(params.lng));
  if (params.reason) form.append("reason", params.reason);
  if (params.onBehalfOfUserId) form.append("on_behalf_of_user_id", params.onBehalfOfUserId);
  if (params.onBehalfReason) form.append("on_behalf_reason", params.onBehalfReason);
  form.append("photo", {
    uri: params.photoUri,
    name: "punch.jpg",
    type: "image/jpeg",
  } as unknown as Blob);
  return apiUpload<CheckInResult>("/api/v1/attendance/punch/checkin", form);
}

export function checkOut(params: {
  lat: number;
  lng: number;
  reason?: string;
  onBehalfOfUserId?: string;
  onBehalfReason?: string;
}): Promise<CheckOutResult> {
  const form = new FormData();
  form.append("lat", String(params.lat));
  form.append("lng", String(params.lng));
  if (params.reason) form.append("reason", params.reason);
  if (params.onBehalfOfUserId) form.append("on_behalf_of_user_id", params.onBehalfOfUserId);
  if (params.onBehalfReason) form.append("on_behalf_reason", params.onBehalfReason);
  return apiUpload<CheckOutResult>("/api/v1/attendance/punch/checkout", form);
}

export type OnBehalfTarget = { id: string; name: string };

// Empty list for EMPLOYEE role (not an error) — ADMIN sees the whole
// tenant, MANAGER sees only their direct reports.
export function listOnBehalfTargets(): Promise<OnBehalfTarget[]> {
  return apiRequest<OnBehalfTarget[]>("/api/v1/attendance/on-behalf-targets");
}

export type CalendarDay = { date: string; status: string };
export type MonthCalendar = { year: number; month: number; month_name: string; days: CalendarDay[] };

// Reuses the exact same day-status logic (incl. the tenant's real Setup >
// Attendance Rules) as the desktop's own calendar — see api_v1/attendance.py.
export function getMonthCalendar(year?: number, month?: number): Promise<MonthCalendar> {
  const params = new URLSearchParams();
  if (year) params.set("year", String(year));
  if (month) params.set("month", String(month));
  const qs = params.toString();
  return apiRequest<MonthCalendar>(`/api/v1/attendance/calendar${qs ? `?${qs}` : ""}`);
}

export function applyLeave(params: {
  leaveType: string;
  startDate: string;
  endDate: string;
  isHalfDay?: boolean;
  reason?: string;
}): Promise<LeaveRequest> {
  return apiRequest<LeaveRequest>("/api/v1/attendance/leave/apply", {
    method: "POST",
    body: {
      leave_type: params.leaveType,
      start_date: params.startDate,
      end_date: params.endDate,
      is_half_day: params.isHalfDay ?? false,
      reason: params.reason,
    },
  });
}
