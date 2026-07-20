import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, ScrollView, StyleSheet, Text, TouchableOpacity, View } from "react-native";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";

import type { AuthStackParamList } from "../navigation/AuthNavigator";
import { ApiError } from "../api/client";
import { listAttendanceRecords, listLeaveRequests, type AttendanceRecord, type LeaveRequest } from "../api/attendance";

const TEAL = "#2DD4BF";
const YELLOW = "#eab308";
const INDIGO = "#6657F2";
const MUTED = "#334155";

type Props = NativeStackScreenProps<AuthStackParamList, "Attendance">;

type DayCell = { num: number | null; status: "present" | "half" | "leave" | "future" | "weekend" | "none" };

function buildMonthCalendar(records: AttendanceRecord[], leave: LeaveRequest[]): DayCell[] {
  const today = new Date();
  const year = today.getFullYear();
  const month = today.getMonth();
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const firstWeekday = new Date(year, month, 1).getDay();

  const byDate = new Map<string, AttendanceRecord>();
  for (const r of records) byDate.set(r.work_date, r);

  const isOnLeave = (d: Date) =>
    leave.some((l) => {
      const start = new Date(l.start_date);
      const end = new Date(l.end_date);
      return d >= start && d <= end && (l.status === "APPROVED" || l.status === "PENDING");
    });

  const cells: DayCell[] = [];
  for (let i = 0; i < firstWeekday; i++) cells.push({ num: null, status: "none" });

  for (let d = 1; d <= daysInMonth; d++) {
    const date = new Date(year, month, d);
    const iso = date.toISOString().slice(0, 10);
    let status: DayCell["status"];
    if (date > today) status = "future";
    else if (date.getDay() === 0 || date.getDay() === 6) status = "weekend";
    else if (isOnLeave(date)) status = "leave";
    else {
      const rec = byDate.get(iso);
      if (rec?.is_half_day) status = "half";
      else if (rec?.check_in_at) status = "present";
      else status = "none";
    }
    cells.push({ num: d, status });
  }
  return cells;
}

const STATUS_STYLE: Record<DayCell["status"], { bg: string; fg: string }> = {
  present: { bg: "rgba(45,212,191,0.16)", fg: TEAL },
  half: { bg: "rgba(234,179,8,0.16)", fg: YELLOW },
  leave: { bg: "rgba(102,87,242,0.16)", fg: INDIGO },
  future: { bg: "transparent", fg: MUTED },
  weekend: { bg: "transparent", fg: MUTED },
  none: { bg: "transparent", fg: "#475569" },
};

const WEEKDAY_LABELS = ["S", "M", "T", "W", "T", "F", "S"];

export default function AttendanceScreen({ navigation }: Props) {
  const [records, setRecords] = useState<AttendanceRecord[]>([]);
  const [leave, setLeave] = useState<LeaveRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [recPage, leavePage] = await Promise.all([listAttendanceRecords(), listLeaveRequests()]);
      setRecords(recPage.items);
      setLeave(leavePage.items);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Couldn't load attendance history.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const monthLabel = new Date().toLocaleDateString(undefined, { month: "long", year: "numeric" });
  const todayLabel = new Date().toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric" });
  const cells = buildMonthCalendar(records, leave);

  return (
    <View style={styles.screen}>
      <View style={styles.header}>
        <TouchableOpacity style={styles.backButton} onPress={() => navigation.goBack()}>
          <Text style={styles.backIcon}>‹</Text>
        </TouchableOpacity>
        <View>
          <Text style={styles.headerTitle}>Attendance</Text>
          <Text style={styles.headerSub}>{todayLabel}</Text>
        </View>
      </View>

      <ScrollView style={styles.body} contentContainerStyle={styles.bodyContent}>
        {/* Punch-in/out, geofencing, and photo capture have no backend endpoint
            yet (app/api_v1/attendance.py is read-only). Flagged to Sahil —
            showing this as an honest disabled state rather than a fake flow. */}
        <View style={styles.punchCard}>
          <View style={styles.punchButton}>
            <Text style={styles.punchIcon}>📷</Text>
          </View>
          <Text style={styles.punchLabel}>Check In / Check Out</Text>
          <Text style={styles.punchHint}>Coming soon — punch-in, location check, and photo capture aren't built in the API yet.</Text>
        </View>

        {error ? <Text style={styles.error}>{error}</Text> : null}
        {loading ? <ActivityIndicator color={TEAL} style={{ marginTop: 16 }} /> : null}

        {!loading ? (
          <>
            <View style={styles.monthHeader}>
              <Text style={styles.monthLabel}>{monthLabel}</Text>
              <View style={styles.legendRow}>
                <Text style={styles.legendItem}><Text style={{ color: TEAL }}>●</Text> Present</Text>
                <Text style={styles.legendItem}><Text style={{ color: YELLOW }}>●</Text> Half</Text>
                <Text style={styles.legendItem}><Text style={{ color: INDIGO }}>●</Text> Leave</Text>
              </View>
            </View>

            <View style={styles.weekdayRow}>
              {WEEKDAY_LABELS.map((wd, i) => (
                <Text key={i} style={styles.weekdayLabel}>{wd}</Text>
              ))}
            </View>
            <View style={styles.calendarGrid}>
              {cells.map((cell, i) => {
                const s = STATUS_STYLE[cell.status];
                return (
                  <View key={i} style={[styles.dayCell, { backgroundColor: s.bg }, cell.num === null && styles.dayCellHidden]}>
                    {cell.num !== null ? <Text style={[styles.dayNum, { color: s.fg }]}>{cell.num}</Text> : null}
                  </View>
                );
              })}
            </View>

            <Text style={styles.sectionTitle}>Leave requests</Text>
            {leave.length === 0 ? (
              <Text style={styles.emptyText}>No leave requests found.</Text>
            ) : (
              leave.map((l) => (
                <View key={l.id} style={styles.leaveRow}>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.leaveType}>{l.leave_type}</Text>
                    <Text style={styles.leaveDates}>{l.start_date} – {l.end_date}{l.is_half_day ? " · Half day" : ""}</Text>
                  </View>
                  <Text style={styles.leaveStatus}>{l.status}</Text>
                </View>
              ))
            )}
          </>
        ) : null}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: "#0b0f1a" },
  header: { paddingTop: 58, paddingHorizontal: 20, paddingBottom: 4, flexDirection: "row", alignItems: "center", gap: 12 },
  backButton: {
    width: 34, height: 34, borderRadius: 10, backgroundColor: "#111827",
    borderWidth: 1, borderColor: "rgba(255,255,255,0.08)", alignItems: "center", justifyContent: "center",
  },
  backIcon: { fontSize: 16, color: "#cbd5e1" },
  headerTitle: { fontSize: 16, fontWeight: "800", color: "#f1f5f9" },
  headerSub: { fontSize: 11.5, color: "#64748b" },
  body: { flex: 1 },
  bodyContent: { padding: 20, paddingBottom: 40 },
  punchCard: { alignItems: "center", paddingVertical: 16 },
  punchButton: {
    width: 128, height: 128, borderRadius: 64, backgroundColor: "#1e293b",
    alignItems: "center", justifyContent: "center",
  },
  punchIcon: { fontSize: 26, opacity: 0.5 },
  punchLabel: { fontSize: 13, fontWeight: "700", color: "#94a3b8", marginTop: 12 },
  punchHint: { fontSize: 11, color: "#64748b", marginTop: 6, textAlign: "center", maxWidth: 260, lineHeight: 16 },
  error: { color: "#f87185", fontSize: 13, marginBottom: 8 },
  monthHeader: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginTop: 8 },
  monthLabel: { fontSize: 13.5, fontWeight: "700", color: "#f1f5f9" },
  legendRow: { flexDirection: "row", gap: 10 },
  legendItem: { fontSize: 10.5, color: "#64748b" },
  weekdayRow: { flexDirection: "row", marginTop: 12, gap: 6 },
  weekdayLabel: { flex: 1, textAlign: "center", fontSize: 10, color: "#475569", fontWeight: "600" },
  calendarGrid: { flexDirection: "row", flexWrap: "wrap", marginTop: 6, gap: 6 },
  dayCell: {
    width: "12.6%", aspectRatio: 1, borderRadius: 9, alignItems: "center", justifyContent: "center",
  },
  dayCellHidden: { opacity: 0 },
  dayNum: { fontSize: 11.5, fontWeight: "600" },
  sectionTitle: { fontSize: 13.5, fontWeight: "700", color: "#f1f5f9", marginTop: 26, marginBottom: 8 },
  emptyText: { fontSize: 12.5, color: "#64748b" },
  leaveRow: {
    flexDirection: "row", alignItems: "center", paddingVertical: 12,
    borderTopWidth: 1, borderColor: "rgba(255,255,255,0.06)",
  },
  leaveType: { fontSize: 12.5, fontWeight: "700", color: "#e2e8f0" },
  leaveDates: { fontSize: 11, color: "#94a3b8", marginTop: 2 },
  leaveStatus: { fontSize: 11, fontWeight: "700", color: "#94a3b8" },
});
