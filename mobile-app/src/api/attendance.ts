import { apiRequest } from "./client";

export type AttendanceRecord = {
  id: string;
  user_id: string;
  work_date: string; // YYYY-MM-DD
  branch_id: string | null;
  check_in_at: string | null;
  check_out_at: string | null;
  is_half_day: boolean;
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

// Punch-in/out, geofencing, and photo capture have no backend endpoint yet
// (app/api_v1/attendance.py is read-only: /records and /leave list history
// only) — flagged to Sahil, not stubbed. Only read history here.
export function listAttendanceRecords(limit = 100): Promise<Page<AttendanceRecord>> {
  return apiRequest<Page<AttendanceRecord>>(`/api/v1/attendance/records?limit=${limit}`);
}

export function listLeaveRequests(limit = 25): Promise<Page<LeaveRequest>> {
  return apiRequest<Page<LeaveRequest>>(`/api/v1/attendance/leave?limit=${limit}`);
}
