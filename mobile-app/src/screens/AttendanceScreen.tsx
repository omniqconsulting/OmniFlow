import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Image,
  Modal,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";
import * as Location from "expo-location";
import * as ImagePicker from "expo-image-picker";
import DateTimePicker from "@react-native-community/datetimepicker";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";

import type { AuthStackParamList } from "../navigation/AuthNavigator";
import { ApiError, API_BASE_URL } from "../api/client";
import {
  applyLeave,
  checkIn,
  checkOut,
  getMonthCalendar,
  getPunchStatus,
  listAttendanceRecords,
  listLeaveRequests,
  listOnBehalfTargets,
  type AttendanceRecord,
  type CalendarDay,
  type LeaveRequest,
  type OnBehalfTarget,
  type PunchStatus,
} from "../api/attendance";
import { formatIstDate, formatIstDateWithWeekday, formatIstTime, getIstYearMonth, toIstIsoDate } from "../utils/dateFormat";

const TEAL = "#2DD4BF";
const YELLOW = "#eab308";
const INDIGO = "#6657F2";
const RED = "#fb7185";
const MUTED = "#334155";

type Props = NativeStackScreenProps<AuthStackParamList, "Attendance">;

// Statuses returned by GET /api/v1/attendance/calendar — same values
// _day_status() in app/attendance.py produces (PRESENT/HALF_DAY/ON_LEAVE/
// ABSENT/WEEKLY_OFF/FUTURE), driven by the tenant's real Setup > Attendance
// Rules, not a client-side approximation.
const STATUS_STYLE: Record<string, { bg: string; fg: string }> = {
  PRESENT: { bg: "rgba(45,212,191,0.16)", fg: TEAL },
  HALF_DAY: { bg: "rgba(234,179,8,0.16)", fg: YELLOW },
  ON_LEAVE: { bg: "rgba(102,87,242,0.16)", fg: INDIGO },
  ABSENT: { bg: "rgba(251,113,133,0.14)", fg: RED },
  WEEKLY_OFF: { bg: "transparent", fg: MUTED },
  FUTURE: { bg: "transparent", fg: MUTED },
};

const WEEKDAY_LABELS = ["S", "M", "T", "W", "T", "F", "S"];
const LEAVE_TYPES: { value: string; label: string }[] = [
  { value: "CASUAL", label: "Casual" },
  { value: "SICK", label: "Sick" },
  { value: "EARNED", label: "Earned" },
];

type PunchState = "none" | "in" | "out";

type PunchDraft = {
  kind: PunchState;
  lat: number;
  lng: number;
  address: string;
  photoUri?: string;
  onBehalfOf?: OnBehalfTarget;
  onBehalfReason?: string;
};

function formatAddress(result: Location.LocationGeocodedAddress | undefined): string {
  if (!result) return "Unknown location";
  const parts = [result.name, result.street, result.city || result.subregion, result.region]
    .filter((p, i, arr) => !!p && arr.indexOf(p) === i);
  return parts.length ? parts.join(", ") : "Unknown location";
}

export default function AttendanceScreen({ navigation }: Props) {
  const initialYearMonth = getIstYearMonth();
  const [year, setYear] = useState(initialYearMonth.year);
  const [month, setMonth] = useState(initialYearMonth.month); // 1-12

  const [calendarDays, setCalendarDays] = useState<CalendarDay[]>([]);
  const [monthName, setMonthName] = useState("");
  const [records, setRecords] = useState<AttendanceRecord[]>([]);
  const [leave, setLeave] = useState<LeaveRequest[]>([]);
  const [status, setStatus] = useState<PunchStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [selectedDay, setSelectedDay] = useState<{ day: CalendarDay; record: AttendanceRecord | null } | null>(null);
  const [dayAddress, setDayAddress] = useState<{ checkIn?: string; checkOut?: string } | null>(null);

  const [leaveFormOpen, setLeaveFormOpen] = useState(false);
  const [leaveType, setLeaveType] = useState("CASUAL");
  const [leaveStart, setLeaveStart] = useState(new Date());
  const [leaveEnd, setLeaveEnd] = useState(new Date());
  const [leaveHalfDay, setLeaveHalfDay] = useState(false);
  const [leaveReason, setLeaveReason] = useState("");
  const [showStartPicker, setShowStartPicker] = useState(false);
  const [showEndPicker, setShowEndPicker] = useState(false);
  const [leaveSubmitting, setLeaveSubmitting] = useState(false);

  const [confirmDraft, setConfirmDraft] = useState<PunchDraft | null>(null);
  const [reasonPrompt, setReasonPrompt] = useState<{ draft: PunchDraft; reason: string } | null>(null);

  // Admin/manager "record on someone's behalf" — empty for EMPLOYEE role.
  const [onBehalfTargets, setOnBehalfTargets] = useState<OnBehalfTarget[]>([]);
  const [selectedTarget, setSelectedTarget] = useState<OnBehalfTarget | null>(null);
  const [targetPickerOpen, setTargetPickerOpen] = useState(false);
  const [onBehalfReasonDraft, setOnBehalfReasonDraft] = useState("");

  const loadStatus = useCallback(async (target: OnBehalfTarget | null) => {
    try {
      const punchStatus = await getPunchStatus(target?.id);
      setStatus(punchStatus);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Couldn't load punch status.");
    }
  }, []);

  const load = useCallback(async (y: number, m: number) => {
    setLoading(true);
    try {
      const [calendar, recordsPage, leavePage, punchStatus, targets] = await Promise.all([
        getMonthCalendar(y, m),
        listAttendanceRecords({ year: y, month: m }),
        listLeaveRequests(),
        getPunchStatus(),
        listOnBehalfTargets(),
      ]);
      setCalendarDays(calendar.days);
      setMonthName(`${calendar.month_name} ${calendar.year}`);
      setRecords(recordsPage.items);
      setLeave(leavePage.items);
      setStatus(punchStatus);
      setOnBehalfTargets(targets);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Couldn't load attendance history.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load(year, month);
  }, [load, year, month]);

  const selectTarget = (target: OnBehalfTarget | null) => {
    setSelectedTarget(target);
    setTargetPickerOpen(false);
    loadStatus(target);
  };

  const goPrevMonth = () => {
    if (month === 1) { setYear((y) => y - 1); setMonth(12); } else { setMonth((m) => m - 1); }
  };
  const goNextMonth = () => {
    if (month === 12) { setYear((y) => y + 1); setMonth(1); } else { setMonth((m) => m + 1); }
  };

  const punchState: PunchState = !status?.record?.check_in_at
    ? "none"
    : !status.record.check_out_at
      ? "in"
      : "out";

  const beginPunch = async (kind: PunchState) => {
    setBusy(true);
    try {
      const { status: locPerm } = await Location.requestForegroundPermissionsAsync();
      if (locPerm !== "granted") {
        Alert.alert(
          "Location needed",
          "OmniFlow needs location access to confirm you're at your workplace. Please allow location access in your device settings and try again.",
        );
        return;
      }
      const pos = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced });
      const lat = pos.coords.latitude;
      const lng = pos.coords.longitude;

      let address = "Unknown location";
      try {
        const results = await Location.reverseGeocodeAsync({ latitude: lat, longitude: lng });
        address = formatAddress(results[0]);
      } catch {
        // Reverse geocoding failure shouldn't block the punch — raw coords
        // are still sent and validated server-side either way.
      }

      let photoUri: string | undefined;
      if (kind === "none") {
        const { status: camPerm } = await ImagePicker.requestCameraPermissionsAsync();
        if (camPerm !== "granted") {
          Alert.alert(
            "Camera needed",
            "OmniFlow needs camera access to capture your check-in photo. Please allow camera access in your device settings and try again.",
          );
          return;
        }
        const result = await ImagePicker.launchCameraAsync({ quality: 0.6, allowsEditing: false });
        if (result.canceled || !result.assets?.[0]) return;
        photoUri = result.assets[0].uri;
      }

      setConfirmDraft({ kind, lat, lng, address, photoUri, onBehalfOf: selectedTarget ?? undefined });
      setOnBehalfReasonDraft("");
    } finally {
      setBusy(false);
    }
  };

  const submitDraft = async (draft: PunchDraft, reason?: string) => {
    setBusy(true);
    try {
      if (draft.kind === "none") {
        const res = await checkIn({
          lat: draft.lat, lng: draft.lng, reason, photoUri: draft.photoUri!,
          onBehalfOfUserId: draft.onBehalfOf?.id, onBehalfReason: draft.onBehalfReason,
        });
        await load(year, month);
        if (selectedTarget) await loadStatus(selectedTarget);
        Alert.alert(
          "Checked in",
          `Check-in recorded${res.recorded_for_name ? ` for ${res.recorded_for_name}` : ""} at ${draft.address}${res.branch_name ? ` · ${res.branch_name}` : ""}.`,
        );
      } else {
        const res = await checkOut({
          lat: draft.lat, lng: draft.lng, reason,
          onBehalfOfUserId: draft.onBehalfOf?.id, onBehalfReason: draft.onBehalfReason,
        });
        await load(year, month);
        if (selectedTarget) await loadStatus(selectedTarget);
        Alert.alert("Checked out", `Check-out recorded${res.recorded_for_name ? ` for ${res.recorded_for_name}` : ""} at ${draft.address}.`);
      }
    } catch (e) {
      if (e instanceof ApiError && e.status === 400 && /outside the office zone/i.test(e.detail)) {
        setReasonPrompt({ draft, reason: "" });
      } else {
        Alert.alert("Couldn't complete that", e instanceof ApiError ? e.detail : "Something went wrong. Please try again.");
      }
    } finally {
      setBusy(false);
    }
  };

  const confirmAndSubmit = () => {
    if (!confirmDraft) return;
    if (confirmDraft.onBehalfOf && !onBehalfReasonDraft.trim()) {
      Alert.alert("Reason required", "Please enter a reason for recording attendance on this employee's behalf.");
      return;
    }
    const draft: PunchDraft = { ...confirmDraft, onBehalfReason: onBehalfReasonDraft.trim() || undefined };
    setConfirmDraft(null);
    submitDraft(draft);
  };

  const submitReasonPrompt = () => {
    if (!reasonPrompt) return;
    if (!reasonPrompt.reason.trim()) {
      Alert.alert("Reason required", "Please enter a reason for checking in/out outside the office zone.");
      return;
    }
    const { draft, reason } = reasonPrompt;
    setReasonPrompt(null);
    submitDraft(draft, reason.trim());
  };

  const openDayDetail = async (day: CalendarDay) => {
    const record = records.find((r) => r.work_date === day.date) ?? null;
    setSelectedDay({ day, record });
    setDayAddress(null);
    if (!record) return;
    const results: { checkIn?: string; checkOut?: string } = {};
    try {
      if (record.check_in_lat != null && record.check_in_lng != null) {
        const geo = await Location.reverseGeocodeAsync({ latitude: record.check_in_lat, longitude: record.check_in_lng });
        results.checkIn = formatAddress(geo[0]);
      }
      if (record.check_out_lat != null && record.check_out_lng != null) {
        const geo = await Location.reverseGeocodeAsync({ latitude: record.check_out_lat, longitude: record.check_out_lng });
        results.checkOut = formatAddress(geo[0]);
      }
    } catch {
      // best-effort only — raw coordinates already shown as a fallback
    }
    setDayAddress(results);
  };

  const leaveDayCount = Math.round((leaveEnd.getTime() - leaveStart.getTime()) / 86400000) + 1;
  const leaveDurationLabel =
    leaveDayCount <= 0
      ? "Select a valid date range"
      : leaveHalfDay && leaveDayCount === 1
        ? "Half day"
        : `${leaveDayCount} day${leaveDayCount === 1 ? "" : "s"}`;

  const submitLeave = async () => {
    if (leaveEnd < leaveStart) {
      Alert.alert("Invalid dates", "End date must be on or after the start date.");
      return;
    }
    setLeaveSubmitting(true);
    try {
      await applyLeave({
        leaveType,
        startDate: toIstIsoDate(leaveStart),
        endDate: toIstIsoDate(leaveEnd),
        isHalfDay: leaveHalfDay,
        reason: leaveReason.trim() || undefined,
      });
      setLeaveFormOpen(false);
      setLeaveReason("");
      setLeaveHalfDay(false);
      await load(year, month);
      Alert.alert("Leave requested", "Your leave request has been submitted for approval.");
    } catch (e) {
      Alert.alert("Couldn't submit", e instanceof ApiError ? e.detail : "Something went wrong. Please try again.");
    } finally {
      setLeaveSubmitting(false);
    }
  };

  const todayLabel = formatIstDateWithWeekday(new Date());
  const punchButtonLabel = punchState === "none" ? "Check In" : punchState === "in" ? "Check Out" : "Done for today";
  const punchIcon = punchState === "out" ? "✓" : "📷";

  const forWhom = selectedTarget ? ` for ${selectedTarget.name}` : "";
  const statusBanner =
    punchState === "none"
      ? { text: `Not checked in yet${forWhom}`, tone: "muted" as const }
      : punchState === "in"
        ? { text: `Checked in${forWhom} at ${formatIstTime(status?.record?.check_in_at)}${status?.record?.branch_name ? ` · ${status.record.branch_name}` : ""}`, tone: "good" as const }
        : { text: `Checked in${forWhom} ${formatIstTime(status?.record?.check_in_at)} · Checked out ${formatIstTime(status?.record?.check_out_at)}${status?.record?.branch_name ? ` · ${status.record.branch_name}` : ""}`, tone: "good" as const };

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
        {onBehalfTargets.length > 0 ? (
          <TouchableOpacity style={styles.onBehalfRow} onPress={() => setTargetPickerOpen(true)}>
            <Text style={styles.onBehalfLabel}>Recording for</Text>
            <Text style={styles.onBehalfValue}>{selectedTarget ? selectedTarget.name : "Myself"} ▾</Text>
          </TouchableOpacity>
        ) : null}

        <View style={[styles.statusBanner, statusBanner.tone === "good" && styles.statusBannerGood]}>
          <View style={[styles.statusDot, statusBanner.tone === "good" && styles.statusDotGood]} />
          <Text style={styles.statusText}>{statusBanner.text}</Text>
        </View>

        <View style={styles.punchCard}>
          <TouchableOpacity
            style={[styles.punchButton, punchState === "out" && styles.punchButtonDone]}
            onPress={() => beginPunch(punchState === "in" ? "in" : "none")}
            disabled={busy || punchState === "out"}
          >
            {busy ? <ActivityIndicator color="#fff" /> : <Text style={styles.punchIcon}>{punchIcon}</Text>}
          </TouchableOpacity>
          <Text style={styles.punchLabel}>{punchButtonLabel}</Text>
          {status?.has_fence ? (
            <Text style={styles.punchHint}>Within {status.fence_radius_m}m of your workplace, or add a reason if you're not.</Text>
          ) : (
            <Text style={styles.punchHint}>No geofence configured for your workplace — any location is accepted.</Text>
          )}
        </View>

        <TouchableOpacity style={styles.leaveButton} onPress={() => setLeaveFormOpen(true)}>
          <Text style={styles.leaveButtonText}>+ Apply for leave</Text>
        </TouchableOpacity>

        {error ? <Text style={styles.error}>{error}</Text> : null}
        {loading ? <ActivityIndicator color={TEAL} style={{ marginTop: 16 }} /> : null}

        {!loading ? (
          <>
            <View style={styles.monthHeader}>
              <TouchableOpacity style={styles.monthArrow} onPress={goPrevMonth}>
                <Text style={styles.monthArrowText}>‹</Text>
              </TouchableOpacity>
              <Text style={styles.monthLabel}>{monthName}</Text>
              <TouchableOpacity style={styles.monthArrow} onPress={goNextMonth}>
                <Text style={styles.monthArrowText}>›</Text>
              </TouchableOpacity>
            </View>
            <View style={styles.legendRow}>
              <Text style={styles.legendItem}><Text style={{ color: TEAL }}>●</Text> Present</Text>
              <Text style={styles.legendItem}><Text style={{ color: YELLOW }}>●</Text> Half</Text>
              <Text style={styles.legendItem}><Text style={{ color: INDIGO }}>●</Text> Leave</Text>
              <Text style={styles.legendItem}><Text style={{ color: RED }}>●</Text> Absent</Text>
            </View>

            <View style={styles.weekdayRow}>
              {WEEKDAY_LABELS.map((wd, i) => (
                <Text key={i} style={styles.weekdayLabel}>{wd}</Text>
              ))}
            </View>
            <View style={styles.calendarGrid}>
              {calendarDays.length > 0 &&
                Array.from({ length: new Date(calendarDays[0].date).getDay() }).map((_, i) => (
                  <View key={`pad-${i}`} style={[styles.dayCell, styles.dayCellHidden]} />
                ))}
              {calendarDays.map((cell, i) => {
                const s = STATUS_STYLE[cell.status] ?? STATUS_STYLE.WEEKLY_OFF;
                const num = new Date(cell.date).getDate();
                return (
                  <TouchableOpacity key={i} style={[styles.dayCell, { backgroundColor: s.bg }]} onPress={() => openDayDetail(cell)}>
                    <Text style={[styles.dayNum, { color: s.fg }]}>{num}</Text>
                  </TouchableOpacity>
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

      {/* Recording-for picker */}
      <Modal visible={targetPickerOpen} transparent animationType="fade" onRequestClose={() => setTargetPickerOpen(false)}>
        <View style={styles.modalBackdrop}>
          <View style={styles.pickerCard}>
            <View style={styles.pickerHeader}>
              <View style={{ flex: 1 }}>
                <Text style={styles.modalTitle}>Record attendance for</Text>
                <Text style={styles.modalSubtitle}>Photo, location, and time are still captured live — this just says who the record is for.</Text>
              </View>
              <TouchableOpacity style={styles.pickerCloseButton} onPress={() => setTargetPickerOpen(false)}>
                <Text style={styles.pickerCloseIcon}>✕</Text>
              </TouchableOpacity>
            </View>
            <ScrollView style={styles.pickerList}>
              <TouchableOpacity
                style={[styles.leaveTypeChip, !selectedTarget && styles.leaveTypeChipActive, { marginBottom: 8, height: 44 }]}
                onPress={() => selectTarget(null)}
              >
                <Text style={[styles.leaveTypeChipText, !selectedTarget && styles.leaveTypeChipTextActive]}>Myself</Text>
              </TouchableOpacity>
              {onBehalfTargets.map((t) => (
                <TouchableOpacity
                  key={t.id}
                  style={[styles.leaveTypeChip, selectedTarget?.id === t.id && styles.leaveTypeChipActive, { marginBottom: 8, height: 44 }]}
                  onPress={() => selectTarget(t)}
                >
                  <Text style={[styles.leaveTypeChipText, selectedTarget?.id === t.id && styles.leaveTypeChipTextActive]}>{t.name}</Text>
                </TouchableOpacity>
              ))}
            </ScrollView>
          </View>
        </View>
      </Modal>

      {/* Location confirmation */}
      <Modal visible={!!confirmDraft} transparent animationType="fade" onRequestClose={() => setConfirmDraft(null)}>
        <View style={styles.modalBackdrop}>
          <View style={styles.modalCard}>
            <Text style={styles.modalTitle}>
              {confirmDraft?.kind === "none" ? "Check in here?" : "Check out here?"}
              {confirmDraft?.onBehalfOf ? ` (for ${confirmDraft.onBehalfOf.name})` : ""}
            </Text>
            <Text style={styles.modalSubtitle}>{confirmDraft?.address}</Text>
            {confirmDraft?.onBehalfOf ? (
              <>
                <Text style={styles.detailLabel}>Reason for recording on their behalf</Text>
                <TextInput
                  style={styles.modalInput}
                  placeholder="e.g. no smartphone of their own"
                  placeholderTextColor="#64748b"
                  value={onBehalfReasonDraft}
                  onChangeText={setOnBehalfReasonDraft}
                  autoFocus
                />
              </>
            ) : null}
            <View style={styles.modalActions}>
              <TouchableOpacity style={styles.modalCancel} onPress={() => setConfirmDraft(null)}>
                <Text style={styles.modalCancelText}>Cancel</Text>
              </TouchableOpacity>
              <TouchableOpacity style={styles.modalSubmit} onPress={confirmAndSubmit}>
                <Text style={styles.modalSubmitText}>Confirm</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>

      {/* Out-of-fence reason */}
      <Modal visible={!!reasonPrompt} transparent animationType="fade" onRequestClose={() => setReasonPrompt(null)}>
        <View style={styles.modalBackdrop}>
          <View style={styles.modalCard}>
            <Text style={styles.modalTitle}>You're outside the office zone</Text>
            <Text style={styles.modalSubtitle}>Add a reason to continue.</Text>
            <TextInput
              style={styles.modalInput}
              placeholder="e.g. client site visit"
              placeholderTextColor="#64748b"
              value={reasonPrompt?.reason ?? ""}
              onChangeText={(t) => setReasonPrompt((p) => (p ? { ...p, reason: t } : p))}
              autoFocus
            />
            <View style={styles.modalActions}>
              <TouchableOpacity style={styles.modalCancel} onPress={() => setReasonPrompt(null)}>
                <Text style={styles.modalCancelText}>Cancel</Text>
              </TouchableOpacity>
              <TouchableOpacity style={styles.modalSubmit} onPress={submitReasonPrompt}>
                <Text style={styles.modalSubmitText}>Submit</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>

      {/* Day detail — photo, check-in/out times, location */}
      <Modal visible={!!selectedDay} transparent animationType="fade" onRequestClose={() => setSelectedDay(null)}>
        <View style={styles.modalBackdrop}>
          <ScrollView style={styles.modalCardScroll} contentContainerStyle={styles.modalCard}>
            <View style={styles.modalHeaderRow}>
              <View style={{ flex: 1 }}>
                <Text style={styles.modalTitle}>{selectedDay ? formatIstDate(new Date(selectedDay.day.date)) : ""}</Text>
                <Text style={styles.modalSubtitle}>{selectedDay?.day.status.replace("_", " ")}</Text>
              </View>
              <TouchableOpacity style={styles.pickerCloseButton} onPress={() => setSelectedDay(null)}>
                <Text style={styles.pickerCloseIcon}>✕</Text>
              </TouchableOpacity>
            </View>

            {selectedDay?.record ? (
              <>
                {selectedDay.record.photo_path ? (
                  <Image source={{ uri: `${API_BASE_URL}${selectedDay.record.photo_path}` }} style={styles.detailPhoto} />
                ) : null}
                <View style={styles.detailRow}>
                  <Text style={styles.detailLabel}>Check-in</Text>
                  <Text style={styles.detailValue}>{formatIstTime(selectedDay.record.check_in_at)}</Text>
                </View>
                {dayAddress?.checkIn ? <Text style={styles.detailAddress}>{dayAddress.checkIn}</Text> : null}
                <View style={styles.detailRow}>
                  <Text style={styles.detailLabel}>Check-out</Text>
                  <Text style={styles.detailValue}>{formatIstTime(selectedDay.record.check_out_at)}</Text>
                </View>
                {dayAddress?.checkOut ? <Text style={styles.detailAddress}>{dayAddress.checkOut}</Text> : null}
                <View style={styles.detailRow}>
                  <Text style={styles.detailLabel}>Branch</Text>
                  <Text style={styles.detailValue}>{selectedDay.record.branch_name ?? "—"}</Text>
                </View>
                {selectedDay.record.check_in_reason ? (
                  <View style={styles.detailRow}>
                    <Text style={styles.detailLabel}>Reason</Text>
                    <Text style={styles.detailValue}>{selectedDay.record.check_in_reason}</Text>
                  </View>
                ) : null}
              </>
            ) : (
              <Text style={styles.emptyText}>No attendance record for this day.</Text>
            )}

            <TouchableOpacity style={[styles.modalSubmit, { marginTop: 16 }]} onPress={() => setSelectedDay(null)}>
              <Text style={styles.modalSubmitText}>Close</Text>
            </TouchableOpacity>
          </ScrollView>
        </View>
      </Modal>

      {/* Apply for leave */}
      <Modal visible={leaveFormOpen} transparent animationType="fade" onRequestClose={() => setLeaveFormOpen(false)}>
        <View style={styles.modalBackdrop}>
          <ScrollView style={styles.modalCardScroll} contentContainerStyle={styles.modalCard}>
            <View style={styles.modalHeaderRow}>
              <View style={{ flex: 1 }}>
                <Text style={styles.modalTitle}>Apply for leave</Text>
                <Text style={styles.modalSubtitle}>Your manager or admin will review and approve this request.</Text>
              </View>
              <TouchableOpacity style={styles.pickerCloseButton} onPress={() => setLeaveFormOpen(false)}>
                <Text style={styles.pickerCloseIcon}>✕</Text>
              </TouchableOpacity>
            </View>

            <Text style={styles.detailLabel}>Type</Text>
            <View style={styles.leaveTypeRow}>
              {LEAVE_TYPES.map((t) => (
                <TouchableOpacity
                  key={t.value}
                  style={[styles.leaveTypeChip, leaveType === t.value && styles.leaveTypeChipActive]}
                  onPress={() => setLeaveType(t.value)}
                >
                  <Text style={[styles.leaveTypeChipText, leaveType === t.value && styles.leaveTypeChipTextActive]}>{t.label}</Text>
                </TouchableOpacity>
              ))}
            </View>

            <Text style={[styles.detailLabel, { marginTop: 12 }]}>Date range</Text>
            <View style={styles.dateRangeRow}>
              <TouchableOpacity style={[styles.modalInput, styles.dateRangeInput]} onPress={() => setShowStartPicker(true)}>
                <Text style={{ color: "#e2e8f0" }}>{formatIstDate(leaveStart)}</Text>
              </TouchableOpacity>
              <Text style={styles.dateRangeArrow}>→</Text>
              <TouchableOpacity style={[styles.modalInput, styles.dateRangeInput]} onPress={() => setShowEndPicker(true)}>
                <Text style={{ color: "#e2e8f0" }}>{formatIstDate(leaveEnd)}</Text>
              </TouchableOpacity>
            </View>
            <Text style={styles.durationHint}>{leaveDurationLabel}</Text>
            {showStartPicker ? (
              <DateTimePicker
                value={leaveStart}
                mode="date"
                onChange={(_, d) => {
                  setShowStartPicker(false);
                  if (!d) return;
                  setLeaveStart(d);
                  if (leaveEnd < d) setLeaveEnd(d);
                }}
              />
            ) : null}
            {showEndPicker ? (
              <DateTimePicker
                value={leaveEnd}
                mode="date"
                minimumDate={leaveStart}
                onChange={(_, d) => { setShowEndPicker(false); if (d) setLeaveEnd(d); }}
              />
            ) : null}

            <TouchableOpacity style={styles.halfDayRow} onPress={() => setLeaveHalfDay((v) => !v)}>
              <View style={[styles.checkbox, leaveHalfDay && styles.checkboxChecked]} />
              <Text style={styles.detailLabel}>Half day (single-day leave only)</Text>
            </TouchableOpacity>

            <Text style={[styles.detailLabel, { marginTop: 12 }]}>Reason (optional)</Text>
            <TextInput
              style={[styles.modalInput, { height: 70, textAlignVertical: "top" }]}
              placeholder="Reason for leave"
              placeholderTextColor="#64748b"
              value={leaveReason}
              onChangeText={setLeaveReason}
              multiline
            />

            <View style={styles.modalActions}>
              <TouchableOpacity style={styles.modalCancel} onPress={() => setLeaveFormOpen(false)}>
                <Text style={styles.modalCancelText}>Cancel</Text>
              </TouchableOpacity>
              <TouchableOpacity style={styles.modalSubmit} onPress={submitLeave} disabled={leaveSubmitting}>
                {leaveSubmitting ? <ActivityIndicator color="#fff" /> : <Text style={styles.modalSubmitText}>Submit</Text>}
              </TouchableOpacity>
            </View>
          </ScrollView>
        </View>
      </Modal>
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
  statusBanner: {
    flexDirection: "row", alignItems: "center", gap: 10, padding: 14, borderRadius: 14,
    backgroundColor: "rgba(148,163,184,0.1)", borderWidth: 1, borderColor: "rgba(148,163,184,0.18)",
  },
  statusBannerGood: { backgroundColor: "rgba(45,212,191,0.1)", borderColor: "rgba(45,212,191,0.25)" },
  statusDot: { width: 10, height: 10, borderRadius: 5, backgroundColor: "#64748b" },
  statusDotGood: { backgroundColor: TEAL },
  statusText: { fontSize: 13, fontWeight: "700", color: "#f1f5f9", flexShrink: 1 },
  punchCard: { alignItems: "center", paddingVertical: 16 },
  punchButton: {
    width: 128, height: 128, borderRadius: 64, backgroundColor: TEAL,
    alignItems: "center", justifyContent: "center",
  },
  punchButtonDone: { backgroundColor: "#1e293b" },
  punchIcon: { fontSize: 26 },
  punchLabel: { fontSize: 13, fontWeight: "700", color: "#f1f5f9", marginTop: 12 },
  punchHint: { fontSize: 11, color: "#64748b", marginTop: 8, textAlign: "center", paddingHorizontal: 24, lineHeight: 16 },
  leaveButton: {
    marginTop: 6, alignSelf: "center", paddingHorizontal: 16, paddingVertical: 10, borderRadius: 10,
    backgroundColor: "rgba(102,87,242,0.14)", borderWidth: 1, borderColor: "rgba(102,87,242,0.3)",
  },
  leaveButtonText: { color: INDIGO, fontWeight: "700", fontSize: 12.5 },
  onBehalfRow: {
    flexDirection: "row", alignItems: "center", justifyContent: "space-between",
    backgroundColor: "#111827", borderWidth: 1, borderColor: "rgba(255,255,255,0.08)",
    borderRadius: 12, paddingHorizontal: 14, paddingVertical: 12, marginBottom: 14,
  },
  onBehalfLabel: { fontSize: 12, color: "#94a3b8", fontWeight: "600" },
  onBehalfValue: { fontSize: 13, color: TEAL, fontWeight: "700" },
  dateRangeRow: { flexDirection: "row", alignItems: "center", gap: 8, marginTop: 6 },
  dateRangeInput: { flex: 1, marginTop: 0 },
  dateRangeArrow: { color: "#64748b", fontSize: 14 },
  durationHint: { fontSize: 11, color: "#64748b", marginTop: 6 },
  pickerCard: {
    width: "100%", maxHeight: "80%", backgroundColor: "#111827", borderRadius: 16,
    borderWidth: 1, borderColor: "rgba(255,255,255,0.08)", overflow: "hidden",
  },
  pickerHeader: { flexDirection: "row", alignItems: "flex-start", gap: 10, padding: 20, paddingBottom: 12 },
  pickerCloseButton: {
    width: 28, height: 28, borderRadius: 8, backgroundColor: "#1e293b",
    alignItems: "center", justifyContent: "center",
  },
  pickerCloseIcon: { color: "#cbd5e1", fontSize: 14 },
  pickerList: { paddingHorizontal: 20, paddingBottom: 20 },
  modalHeaderRow: { flexDirection: "row", alignItems: "flex-start", gap: 10 },
  error: { color: "#f87185", fontSize: 13, marginBottom: 8 },
  monthHeader: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 18, marginTop: 20 },
  monthArrow: { width: 30, height: 30, borderRadius: 8, backgroundColor: "#111827", alignItems: "center", justifyContent: "center" },
  monthArrowText: { fontSize: 16, color: "#cbd5e1" },
  monthLabel: { fontSize: 13.5, fontWeight: "700", color: "#f1f5f9", minWidth: 130, textAlign: "center" },
  legendRow: { flexDirection: "row", gap: 10, justifyContent: "center", marginTop: 8 },
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
  modalBackdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.6)", alignItems: "center", justifyContent: "center", padding: 24 },
  modalCardScroll: { width: "100%", maxHeight: "80%" },
  modalCard: { width: "100%", backgroundColor: "#111827", borderRadius: 16, padding: 20, borderWidth: 1, borderColor: "rgba(255,255,255,0.08)" },
  modalTitle: { fontSize: 15, fontWeight: "800", color: "#f1f5f9" },
  modalSubtitle: { fontSize: 12.5, color: "#94a3b8", marginTop: 4, marginBottom: 14 },
  modalInput: {
    height: 46, borderRadius: 10, backgroundColor: "#0d1424", borderWidth: 1, borderColor: "rgba(255,255,255,0.1)",
    color: "#e2e8f0", fontSize: 14, paddingHorizontal: 12, justifyContent: "center", marginTop: 6,
  },
  modalActions: { flexDirection: "row", gap: 10, marginTop: 16 },
  modalCancel: { flex: 1, height: 44, borderRadius: 10, alignItems: "center", justifyContent: "center", backgroundColor: "#1e293b" },
  modalCancelText: { color: "#cbd5e1", fontWeight: "700", fontSize: 13 },
  modalSubmit: { flex: 1, height: 44, borderRadius: 10, alignItems: "center", justifyContent: "center", backgroundColor: INDIGO },
  modalSubmitText: { color: "#fff", fontWeight: "700", fontSize: 13 },
  detailPhoto: { width: "100%", height: 180, borderRadius: 12, marginBottom: 14, backgroundColor: "#0d1424" },
  detailRow: { flexDirection: "row", justifyContent: "space-between", paddingVertical: 8, borderTopWidth: 1, borderColor: "rgba(255,255,255,0.06)" },
  detailLabel: { fontSize: 12, fontWeight: "600", color: "#94a3b8" },
  detailValue: { fontSize: 12.5, fontWeight: "700", color: "#e2e8f0" },
  detailAddress: { fontSize: 11, color: "#64748b", marginTop: -4, marginBottom: 4 },
  leaveTypeRow: { flexDirection: "row", gap: 8, marginTop: 6 },
  leaveTypeChip: { flex: 1, height: 40, borderRadius: 10, alignItems: "center", justifyContent: "center", backgroundColor: "#0d1424", borderWidth: 1, borderColor: "rgba(255,255,255,0.1)" },
  leaveTypeChipActive: { backgroundColor: "rgba(102,87,242,0.16)", borderColor: INDIGO },
  leaveTypeChipText: { color: "#94a3b8", fontSize: 12.5, fontWeight: "600" },
  leaveTypeChipTextActive: { color: INDIGO },
  halfDayRow: { flexDirection: "row", alignItems: "center", gap: 10, marginTop: 14 },
  checkbox: { width: 20, height: 20, borderRadius: 6, borderWidth: 1.5, borderColor: "rgba(255,255,255,0.2)" },
  checkboxChecked: { backgroundColor: INDIGO, borderColor: INDIGO },
});
