import { useCallback, useEffect, useMemo, useState } from "react";
import { ActivityIndicator, Alert, ScrollView, StyleSheet, Text, TextInput, TouchableOpacity, View } from "react-native";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import * as ImagePicker from "expo-image-picker";

import type { AuthStackParamList } from "../navigation/AuthNavigator";
import { ApiError } from "../api/client";
import {
  completeChecklist,
  createChecklistTemplate,
  deleteChecklistTemplate,
  failChecklist,
  getChecklistFilterOptions,
  getChecklistHistory,
  listChecklists,
  notifyChecklist,
  updateChecklistTemplate,
  uploadChecklistEvidence,
  type ChecklistFormInput,
  type ChecklistFreqType,
  type ChecklistHistory,
  type ChecklistItem,
  type FilterOptions,
} from "../api/checklists";
import type { EmployeeOption } from "../api/tickets";
import BottomSheet from "../components/BottomSheet";
import EmployeePicker from "../components/EmployeePicker";

const TEAL = "#2DD4BF";
const INDIGO = "#6657F2";
const RED = "#ef4444";
const AMBER = "#f59e0b";
const GREEN = "#22c55e";
const BLUE = "#3b82f6";
const CYAN = "#06b6d4";
const PURPLE = "#a78bfa";
const YELLOW = "#eab308";
const GRAY_FG = "#cbd5e1";
const GRAY_BG = "rgba(148,163,184,0.14)";

type Props = NativeStackScreenProps<AuthStackParamList, "Checklists">;

const FREQ_BUCKET_META: Record<string, { label: string; fg: string; bg: string }> = {
  DAILY: { label: "Daily", fg: TEAL, bg: "rgba(45,212,191,0.14)" },
  WEEKLY: { label: "Weekly", fg: INDIGO, bg: "rgba(102,87,242,0.14)" },
  MONTHLY: { label: "Monthly", fg: BLUE, bg: "rgba(59,130,246,0.14)" },
  QUARTERLY: { label: "Quarterly", fg: CYAN, bg: "rgba(6,182,212,0.14)" },
  YEARLY: { label: "Yearly", fg: PURPLE, bg: "rgba(167,139,250,0.14)" },
  CUSTOM: { label: "Custom", fg: YELLOW, bg: "rgba(234,179,8,0.14)" },
};
const STATUS_META: Record<string, { label: string; fg: string; bg: string }> = {
  PENDING: { label: "Pending", fg: GRAY_FG, bg: GRAY_BG },
  IN_PROGRESS: { label: "In Progress", fg: BLUE, bg: "rgba(59,130,246,0.14)" },
  OVERDUE: { label: "Overdue", fg: AMBER, bg: "rgba(245,158,11,0.14)" },
  DONE: { label: "Done", fg: GREEN, bg: "rgba(34,197,94,0.14)" },
  FAILED: { label: "Failed", fg: RED, bg: "rgba(239,68,68,0.14)" },
};
const FREQ_TYPE_DEFS: { key: ChecklistFreqType; label: string; bucket: string }[] = [
  { key: "DAILY", label: "Daily", bucket: "DAILY" },
  { key: "WEEKLY", label: "Weekly", bucket: "WEEKLY" },
  { key: "MONTHLY", label: "Monthly", bucket: "MONTHLY" },
  { key: "QUARTERLY", label: "Quarterly", bucket: "QUARTERLY" },
  { key: "YEARLY", label: "Yearly", bucket: "YEARLY" },
  { key: "WEEKLY_CUSTOM", label: "By Weekday", bucket: "CUSTOM" },
  { key: "MONTHLY_DATE", label: "By Month Date", bucket: "CUSTOM" },
  { key: "YEARLY_DATE", label: "By Year Date", bucket: "CUSTOM" },
  { key: "NTH_WEEKDAY_MONTH", label: "Nth Weekday/Mo", bucket: "CUSTOM" },
  { key: "NTH_WEEKDAY_QUARTER", label: "Nth Weekday/Qtr", bucket: "CUSTOM" },
];
const DOW = [{ v: 0, l: "Mon" }, { v: 1, l: "Tue" }, { v: 2, l: "Wed" }, { v: 3, l: "Thu" }, { v: 4, l: "Fri" }, { v: 5, l: "Sat" }, { v: 6, l: "Sun" }];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const NTHS = [{ v: 1, l: "1st" }, { v: 2, l: "2nd" }, { v: 3, l: "3rd" }, { v: 4, l: "4th" }, { v: -1, l: "Last" }];

function freqBucketOf(freqType: string | null): string {
  return FREQ_TYPE_DEFS.find((f) => f.key === freqType)?.bucket ?? "DAILY";
}

function toggleInArray<T>(arr: T[], val: T): T[] {
  return arr.includes(val) ? arr.filter((v) => v !== val) : [...arr, val];
}

function fmtDue(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", day: "numeric", month: "short", hour: "numeric", minute: "2-digit", hour12: true });
}

function daysUntil(iso: string | null): number {
  if (!iso) return 9999;
  const ms = new Date(iso).getTime() - Date.now();
  return Math.floor(ms / 86400000);
}

// IST calendar-day compare (not UTC) — matches the rest of the app's IST
// date handling (see mobile-app/src/utils/dateFormat.ts).
function istDateStr(d: Date): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit", day: "2-digit" }).format(d);
}
function isDueToday(iso: string | null): boolean {
  return !!iso && istDateStr(new Date(iso)) === istDateStr(new Date());
}

function emptyForm(): ChecklistFormInput & { assignMode: "EMPLOYEE" | "ROLE" } {
  return {
    title: "", description: "", frequency_type: "DAILY", dow_days: [], is_recurring: true,
    due_time_mode: "ANYTIME", due_time: "", evidence_required: false,
    assigned_to_role: "EMPLOYEE", assignMode: "EMPLOYEE",
  };
}

export default function ChecklistsScreen({ navigation, route }: Props) {
  const { user } = route.params;
  const canManage = user.role === "ADMIN" || user.role === "MANAGER";
  const isAdmin = user.role === "ADMIN";

  const [items, setItems] = useState<ChecklistItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [options, setOptions] = useState<FilterOptions | null>(null);

  const [view, setView] = useState<"TODAY" | "OVERDUE" | "UPCOMING" | "ALL">("TODAY");
  const [failedOpen, setFailedOpen] = useState(false);

  const [filterOpen, setFilterOpen] = useState(false);
  const [filterWindow, setFilterWindow] = useState<number | null>(null);
  const [filterFreqBucket, setFilterFreqBucket] = useState<string[]>([]);
  const [filterDept, setFilterDept] = useState<string[]>([]);
  const [filterBranch, setFilterBranch] = useState<string[]>([]);
  const [filterManager, setFilterManager] = useState<string[]>([]);
  const [filterEmployee, setFilterEmployee] = useState<string[]>([]);

  const [formOpen, setFormOpen] = useState(false);
  const [editingTemplateId, setEditingTemplateId] = useState<string | null>(null);
  const [formData, setFormData] = useState(emptyForm());
  const [employeePickerOpen, setEmployeePickerOpen] = useState(false);
  const [saving, setSaving] = useState(false);

  const [detailItem, setDetailItem] = useState<ChecklistItem | null>(null);
  const [completingItem, setCompletingItem] = useState<ChecklistItem | null>(null);
  const [completeNote, setCompleteNote] = useState("");
  const [evidenceUri, setEvidenceUri] = useState<string | null>(null);
  const [uploadingEvidence, setUploadingEvidence] = useState(false);
  const [historyItem, setHistoryItem] = useState<{ item: ChecklistItem; data: ChecklistHistory } | null>(null);

  const [notifiedId, setNotifiedId] = useState<string | null>(null);
  const [armedDeleteId, setArmedDeleteId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const rows = await listChecklists();
      setItems(rows);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Couldn't load checklists.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!canManage) return;
    getChecklistFilterOptions().then(setOptions).catch(() => {});
  }, [canManage]);

  const employeeOptions: EmployeeOption[] = useMemo(
    () => (options?.employees ?? []).map((e) => ({ id: e.id, name: e.name })),
    [options]
  );

  const matchesFilters = useCallback(
    (i: ChecklistItem) => {
      const bucket = freqBucketOf(i.frequency_type);
      if (filterFreqBucket.length && !filterFreqBucket.includes(bucket)) return false;
      if (canManage && filterDept.length) {
        const dept = options?.departments.find((d) => d.name === i.department_name);
        if (!dept || !filterDept.includes(dept.id)) return false;
      }
      if (canManage && filterBranch.length) {
        const br = options?.branches.find((b) => b.name === i.branch_name);
        if (!br || !filterBranch.includes(br.id)) return false;
      }
      if (isAdmin && filterManager.length) {
        const mgr = options?.managers.find((m) => m.name === i.manager_name);
        if (!mgr || !filterManager.includes(mgr.id)) return false;
      }
      if (canManage && filterEmployee.length && i.employee_id && !filterEmployee.includes(i.employee_id)) return false;
      return true;
    },
    [filterFreqBucket, filterDept, filterBranch, filterManager, filterEmployee, options, canManage, isAdmin]
  );

  const filtered = useMemo(() => (items ?? []).filter(matchesFilters), [items, matchesFilters]);
  const todayItems = useMemo(() => filtered.filter((i) => isDueToday(i.due_at)), [filtered]);
  const overdueItems = useMemo(() => filtered.filter((i) => i.status === "OVERDUE"), [filtered]);
  const upcomingAll = useMemo(() => filtered.filter((i) => i.status === "PENDING" || i.status === "IN_PROGRESS"), [filtered]);
  const upcomingItems = useMemo(
    () => (filterWindow == null ? upcomingAll : upcomingAll.filter((i) => daysUntil(i.due_at) <= filterWindow)),
    [upcomingAll, filterWindow]
  );
  const failedItems = useMemo(() => filtered.filter((i) => i.status === "FAILED"), [filtered]);
  const allItems = filtered;

  const viewDefs = canManage
    ? [
        { key: "TODAY" as const, label: "Today", icon: "🗓", count: todayItems.length },
        { key: "OVERDUE" as const, label: "Overdue", icon: "⚠", count: overdueItems.length },
        { key: "UPCOMING" as const, label: "Upcoming", icon: "📅", count: upcomingItems.length },
        { key: "ALL" as const, label: "All", icon: "☰", count: allItems.length },
      ]
    : [
        { key: "TODAY" as const, label: "Today", icon: "🗓", count: todayItems.length },
        { key: "OVERDUE" as const, label: "Overdue", icon: "⚠", count: overdueItems.length },
        { key: "UPCOMING" as const, label: "Upcoming", icon: "📅", count: upcomingItems.length },
      ];
  const viewMap = { TODAY: todayItems, OVERDUE: overdueItems, UPCOMING: upcomingItems, ALL: allItems };
  const visibleItems = viewMap[view] ?? [];
  const emptyLabel =
    view === "TODAY" ? "Nothing due today." :
    view === "OVERDUE" ? "No overdue checklists — all caught up!" : view === "UPCOMING" ? "Nothing upcoming in this window." : "No checklists match these filters.";

  const doneToday = filtered.filter((i) => i.status === "DONE").length;
  const totalToday = filtered.length;
  const compliancePct = totalToday ? Math.round((doneToday / totalToday) * 100) : 100;

  const activeFilterCount =
    filterFreqBucket.length + filterDept.length + filterBranch.length + filterManager.length + filterEmployee.length + (filterWindow != null ? 1 : 0);

  const chipPair = (active: boolean, color: string) => ({
    box: [styles.chip, active ? { backgroundColor: color + "26", borderColor: color } : styles.chipInactive],
    text: [styles.chipText, { color: active ? color : "#94a3b8" }],
  });

  // ── Actions ──────────────────────────────────────────────────────────

  const openCreate = () => {
    setEditingTemplateId(null);
    setFormData(emptyForm());
    setFormOpen(true);
  };

  const openEdit = (item: ChecklistItem) => {
    setEditingTemplateId(item.template_id);
    setFormData({
      title: item.title,
      description: item.description,
      frequency_type: item.frequency_type ?? "DAILY",
      evidence_required: item.evidence_required,
      is_recurring: true,
      due_time_mode: "ANYTIME",
      assignMode: "EMPLOYEE",
      assigned_to_user_id: item.employee_id ?? undefined,
      dow_days: [],
    });
    setDetailItem(null);
    setFormOpen(true);
  };

  const submitForm = async () => {
    if (!formData.title.trim()) {
      Alert.alert("Title is required");
      return;
    }
    if (formData.assignMode === "EMPLOYEE" && !formData.assigned_to_user_id) {
      Alert.alert("Select who this checklist is assigned to");
      return;
    }
    setSaving(true);
    try {
      const body: ChecklistFormInput = {
        title: formData.title.trim(),
        description: formData.description?.trim() || "",
        frequency_type: formData.frequency_type,
        dow_days: formData.dow_days,
        dom_day: formData.dom_day,
        doy_month: formData.doy_month,
        doy_day: formData.doy_day,
        nth: formData.nth,
        nth_weekday: formData.nth_weekday,
        is_recurring: formData.is_recurring,
        due_time_mode: formData.due_time_mode,
        due_time: formData.due_time_mode === "FIXED_TIME" ? formData.due_time : undefined,
        evidence_required: formData.evidence_required,
        assigned_to_user_id: formData.assignMode === "EMPLOYEE" ? formData.assigned_to_user_id : undefined,
        assigned_to_role: formData.assignMode === "ROLE" ? (formData.assigned_to_role ?? "EMPLOYEE") : undefined,
        assigned_to_dept_id: formData.assignMode === "ROLE" ? formData.assigned_to_dept_id : undefined,
      };
      if (editingTemplateId) {
        await updateChecklistTemplate(editingTemplateId, body);
      } else {
        await createChecklistTemplate(body);
      }
      setFormOpen(false);
      await load();
    } catch (e) {
      Alert.alert("Couldn't save", e instanceof ApiError ? e.detail : "Something went wrong.");
    } finally {
      setSaving(false);
    }
  };

  const openComplete = (item: ChecklistItem) => {
    setCompletingItem(item);
    setCompleteNote("");
    setEvidenceUri(null);
    setDetailItem(null);
  };

  const attachEvidence = async () => {
    const perm = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!perm.granted) return;
    const result = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ImagePicker.MediaTypeOptions.Images, quality: 0.7 });
    if (result.canceled || !result.assets[0]) return;
    if (!completingItem?.assignment_id) return;
    setUploadingEvidence(true);
    try {
      await uploadChecklistEvidence(completingItem.assignment_id, result.assets[0].uri);
      setEvidenceUri(result.assets[0].uri);
    } catch (e) {
      Alert.alert("Couldn't attach evidence", e instanceof ApiError ? e.detail : "Something went wrong.");
    } finally {
      setUploadingEvidence(false);
    }
  };

  const patchItem = (updated: ChecklistItem) => {
    setItems((prev) => (prev ? prev.map((i) => (i.template_id === updated.template_id && i.employee_id === updated.employee_id ? updated : i)) : prev));
  };

  const markComplete = async () => {
    if (!completingItem?.assignment_id) return;
    const overdue = completingItem.status === "OVERDUE";
    if (overdue && !completeNote.trim()) {
      Alert.alert("Delay reason is required for overdue checklists");
      return;
    }
    if (completingItem.evidence_required && !evidenceUri) {
      Alert.alert("Evidence is required — attach a photo or file before completing");
      return;
    }
    try {
      const updated = await completeChecklist(completingItem.assignment_id, completeNote.trim());
      patchItem(updated);
      setCompletingItem(null);
    } catch (e) {
      Alert.alert("Couldn't complete", e instanceof ApiError ? e.detail : "Something went wrong.");
    }
  };

  const markFailed = async () => {
    if (!completingItem?.assignment_id) return;
    try {
      const updated = await failChecklist(completingItem.assignment_id, completeNote.trim());
      patchItem(updated);
      setCompletingItem(null);
    } catch (e) {
      Alert.alert("Couldn't mark as failed", e instanceof ApiError ? e.detail : "Something went wrong.");
    }
  };

  const notifySelected = async () => {
    if (!detailItem?.assignment_id) return;
    try {
      await notifyChecklist(detailItem.assignment_id);
      setNotifiedId(detailItem.assignment_id);
      setTimeout(() => setNotifiedId((cur) => (cur === detailItem.assignment_id ? null : cur)), 1600);
    } catch (e) {
      Alert.alert("Couldn't notify", e instanceof ApiError ? e.detail : "Something went wrong.");
    }
  };

  const openHistory = async (item: ChecklistItem) => {
    try {
      const data = await getChecklistHistory(item.template_id, item.employee_id ?? undefined);
      setHistoryItem({ item, data });
    } catch (e) {
      Alert.alert("Couldn't load history", e instanceof ApiError ? e.detail : "Something went wrong.");
    }
  };

  const deleteSelected = async () => {
    if (!detailItem) return;
    if (armedDeleteId !== detailItem.template_id) {
      setArmedDeleteId(detailItem.template_id);
      return;
    }
    try {
      await deleteChecklistTemplate(detailItem.template_id);
      setItems((prev) => (prev ? prev.filter((i) => i.template_id !== detailItem.template_id) : prev));
      setDetailItem(null);
      setArmedDeleteId(null);
    } catch (e) {
      Alert.alert("Couldn't delete", e instanceof ApiError ? e.detail : "Something went wrong.");
    }
  };

  // ── Render ───────────────────────────────────────────────────────────

  return (
    <View style={styles.screen}>
      <View style={styles.topBar}>
        <View style={styles.topBarLeft}>
          <TouchableOpacity style={styles.iconButton} onPress={() => navigation.goBack()}>
            <Text style={styles.backIcon}>‹</Text>
          </TouchableOpacity>
          <Text style={styles.title}>Checklists</Text>
        </View>
        <View style={styles.topBarRight}>
          {canManage ? (
            <TouchableOpacity style={styles.iconButton} onPress={() => setFilterOpen(true)}>
              <Text style={styles.filterIcon}>▤</Text>
              {activeFilterCount ? (
                <View style={styles.badge}>
                  <Text style={styles.badgeText}>{activeFilterCount}</Text>
                </View>
              ) : null}
            </TouchableOpacity>
          ) : null}
          {canManage ? (
            <TouchableOpacity style={styles.addButton} onPress={openCreate}>
              <Text style={styles.addButtonText}>+</Text>
            </TouchableOpacity>
          ) : null}
        </View>
      </View>

      <View style={styles.complianceStrip}>
        <View>
          <Text style={styles.complianceTitle}>Today's compliance</Text>
          <Text style={styles.complianceSub}>{doneToday} of {totalToday} completed on time</Text>
        </View>
        <Text style={styles.compliancePct}>{compliancePct}%</Text>
      </View>

      <View style={styles.viewTabs}>
        {viewDefs.map((v) => {
          const active = view === v.key;
          const danger = v.key === "OVERDUE";
          return (
            <TouchableOpacity
              key={v.key}
              style={[styles.viewTab, active && (danger ? styles.viewTabDanger : styles.viewTabActive)]}
              onPress={() => setView(v.key)}
            >
              <Text style={[styles.viewTabIcon, { color: active ? (danger ? RED : TEAL) : "#64748b" }]}>{v.icon}</Text>
              <Text style={[styles.viewTabLabel, { color: active ? (danger ? RED : TEAL) : "#94a3b8" }]}>{v.label}</Text>
              {v.count > 0 ? <Text style={[styles.viewTabBadge, { color: active ? (danger ? RED : TEAL) : "#94a3b8" }]}>{v.count}</Text> : null}
            </TouchableOpacity>
          );
        })}
      </View>

      {error ? <Text style={styles.error}>{error}</Text> : null}
      {loading && !items ? <ActivityIndicator color={TEAL} style={{ marginTop: 24 }} /> : null}

      <ScrollView style={styles.body} contentContainerStyle={styles.bodyContent}>
        {visibleItems.map((item) => {
          const bucket = freqBucketOf(item.frequency_type);
          const fm = FREQ_BUCKET_META[bucket];
          const sm = STATUS_META[item.status ?? "PENDING"];
          const metaText = canManage ? `${item.employee_name ?? "—"} · ${fmtDue(item.due_at)}` : item.description.slice(0, 60);
          const dueColor = item.status === "OVERDUE" ? AMBER : item.status === "FAILED" ? RED : "#64748b";
          return (
            <TouchableOpacity key={`${item.template_id}:${item.employee_id}`} style={styles.itemCard} onPress={() => setDetailItem(item)}>
              <View style={styles.itemHeader}>
                <View style={[styles.itemDot, { backgroundColor: sm.fg }]} />
                <View style={{ flex: 1, minWidth: 0 }}>
                  <Text style={styles.itemTitle}>{item.title}</Text>
                  <Text style={styles.itemMeta}>{metaText}</Text>
                </View>
              </View>
              <View style={styles.itemFooter}>
                <View style={[styles.pill, { backgroundColor: fm.bg }]}>
                  <Text style={[styles.pillText, { color: fm.fg }]}>{fm.label}</Text>
                </View>
                {item.evidence_required ? <Text style={{ fontSize: 12 }}>📎</Text> : null}
                {view === "ALL" ? (
                  <View style={[styles.pill, { backgroundColor: GRAY_BG }]}>
                    <Text style={[styles.pillText, { color: GRAY_FG, fontSize: 10.5 }]}>{item.compliance_pct}%</Text>
                  </View>
                ) : null}
                <Text style={[styles.itemDue, { color: dueColor }]}>{fmtDue(item.due_at)}</Text>
              </View>
            </TouchableOpacity>
          );
        })}

        {visibleItems.length === 0 && !loading ? (
          <View style={styles.emptyWrap}>
            <Text style={styles.emptyIcon}>✅</Text>
            <Text style={styles.emptyLabel}>{emptyLabel}</Text>
          </View>
        ) : null}

        {view === "OVERDUE" && canManage && failedItems.length > 0 ? (
          <View style={styles.failedPanel}>
            <TouchableOpacity style={styles.failedHeader} onPress={() => setFailedOpen((v) => !v)}>
              <Text style={styles.failedTitle}>✗ Recently Failed ({failedItems.length})</Text>
              <Text style={styles.failedToggle}>{failedOpen ? "Hide ▲" : "Show ▾"}</Text>
            </TouchableOpacity>
            {failedOpen
              ? failedItems.map((f) => (
                  <View key={`${f.template_id}:${f.employee_id}`} style={styles.failedRow}>
                    <Text style={styles.failedItemTitle}>{f.title}</Text>
                    <Text style={styles.failedItemMeta}>{f.employee_name} · Was due {fmtDue(f.due_at)}</Text>
                    {f.failure_note ? <Text style={styles.failedItemNote}>{f.failure_note}</Text> : null}
                  </View>
                ))
              : null}
          </View>
        ) : null}
      </ScrollView>

      {/* Filter sheet */}
      <BottomSheet visible={filterOpen} onClose={() => setFilterOpen(false)}>
        <Text style={styles.sheetTitle}>Filter Checklists</Text>

        <Text style={styles.sheetLabel}>Due window</Text>
        <View style={styles.chipWrap}>
          {[{ v: 0, l: "Today" }, { v: 1, l: "Tomorrow" }, { v: 3, l: "3 days" }, { v: 7, l: "7 days" }, { v: 14, l: "14 days" }, { v: 30, l: "30 days" }].map((w) => {
            const active = filterWindow === w.v;
            const p = chipPair(active, TEAL);
            return (
              <TouchableOpacity key={w.v} style={p.box as any} onPress={() => setFilterWindow(active ? null : w.v)}>
                <Text style={p.text as any}>{w.l}</Text>
              </TouchableOpacity>
            );
          })}
        </View>

        <Text style={styles.sheetLabel}>Frequency</Text>
        <View style={styles.chipWrap}>
          {Object.keys(FREQ_BUCKET_META).map((k) => {
            const active = filterFreqBucket.includes(k);
            const p = chipPair(active, FREQ_BUCKET_META[k].fg);
            return (
              <TouchableOpacity key={k} style={p.box as any} onPress={() => setFilterFreqBucket((prev) => toggleInArray(prev, k))}>
                <Text style={p.text as any}>{FREQ_BUCKET_META[k].label}</Text>
              </TouchableOpacity>
            );
          })}
        </View>

        <Text style={styles.sheetLabel}>Department</Text>
        <View style={styles.chipWrap}>
          {(options?.departments ?? []).map((d) => {
            const active = filterDept.includes(d.id);
            const p = chipPair(active, INDIGO);
            return (
              <TouchableOpacity key={d.id} style={p.box as any} onPress={() => setFilterDept((prev) => toggleInArray(prev, d.id))}>
                <Text style={p.text as any}>{d.name}</Text>
              </TouchableOpacity>
            );
          })}
        </View>

        <Text style={styles.sheetLabel}>Branch</Text>
        <View style={styles.chipWrap}>
          {(options?.branches ?? []).map((b) => {
            const active = filterBranch.includes(b.id);
            const p = chipPair(active, BLUE);
            return (
              <TouchableOpacity key={b.id} style={p.box as any} onPress={() => setFilterBranch((prev) => toggleInArray(prev, b.id))}>
                <Text style={p.text as any}>{b.name}</Text>
              </TouchableOpacity>
            );
          })}
        </View>

        {isAdmin ? (
          <>
            <Text style={styles.sheetLabel}>Manager</Text>
            <View style={styles.chipWrap}>
              {(options?.managers ?? []).map((m) => {
                const active = filterManager.includes(m.id);
                const p = chipPair(active, PURPLE);
                return (
                  <TouchableOpacity key={m.id} style={p.box as any} onPress={() => setFilterManager((prev) => toggleInArray(prev, m.id))}>
                    <Text style={p.text as any}>{m.name}</Text>
                  </TouchableOpacity>
                );
              })}
            </View>
          </>
        ) : null}

        <Text style={styles.sheetLabel}>Employee</Text>
        <View style={styles.chipWrap}>
          {(options?.employees ?? []).map((e) => {
            const active = filterEmployee.includes(e.id);
            const p = chipPair(active, TEAL);
            return (
              <TouchableOpacity key={e.id} style={p.box as any} onPress={() => setFilterEmployee((prev) => toggleInArray(prev, e.id))}>
                <Text style={p.text as any}>{e.name}</Text>
              </TouchableOpacity>
            );
          })}
        </View>

        <View style={styles.sheetActions}>
          <TouchableOpacity
            style={styles.clearButton}
            onPress={() => {
              setFilterWindow(null);
              setFilterFreqBucket([]);
              setFilterDept([]);
              setFilterBranch([]);
              setFilterManager([]);
              setFilterEmployee([]);
            }}
          >
            <Text style={styles.clearButtonText}>Clear</Text>
          </TouchableOpacity>
          <TouchableOpacity style={styles.applyButton} onPress={() => setFilterOpen(false)}>
            <Text style={styles.applyButtonText}>Apply</Text>
          </TouchableOpacity>
        </View>
      </BottomSheet>

      {/* Create / Edit sheet */}
      <BottomSheet visible={formOpen} onClose={() => setFormOpen(false)}>
        <Text style={styles.sheetTitle}>{editingTemplateId ? "Edit Checklist" : "New Checklist"}</Text>

        <Text style={styles.sheetLabel}>Title</Text>
        <TextInput
          style={styles.input}
          placeholder="e.g. Daily Machine Inspection"
          placeholderTextColor="#64748b"
          value={formData.title}
          onChangeText={(v) => setFormData((s) => ({ ...s, title: v }))}
        />

        <Text style={[styles.sheetLabel, { marginTop: 14 }]}>Instructions</Text>
        <TextInput
          style={[styles.input, styles.inputMultiline]}
          placeholder="Step-by-step instructions for this task…"
          placeholderTextColor="#64748b"
          value={formData.description}
          onChangeText={(v) => setFormData((s) => ({ ...s, description: v }))}
          multiline
        />

        <Text style={[styles.sheetLabel, { marginTop: 14 }]}>Frequency type</Text>
        <View style={styles.chipWrap}>
          {FREQ_TYPE_DEFS.map((ft) => {
            const active = formData.frequency_type === ft.key;
            const p = chipPair(active, FREQ_BUCKET_META[ft.bucket].fg);
            return (
              <TouchableOpacity key={ft.key} style={p.box as any} onPress={() => setFormData((s) => ({ ...s, frequency_type: ft.key }))}>
                <Text style={p.text as any}>{ft.label}</Text>
              </TouchableOpacity>
            );
          })}
        </View>

        {formData.frequency_type === "WEEKLY_CUSTOM" ? (
          <>
            <Text style={styles.sheetLabel}>Days of week</Text>
            <View style={styles.chipWrap}>
              {DOW.map((d) => {
                const active = (formData.dow_days ?? []).includes(d.v);
                const p = chipPair(active, INDIGO);
                return (
                  <TouchableOpacity key={d.v} style={p.box as any} onPress={() => setFormData((s) => ({ ...s, dow_days: toggleInArray(s.dow_days ?? [], d.v) }))}>
                    <Text style={p.text as any}>{d.l}</Text>
                  </TouchableOpacity>
                );
              })}
            </View>
          </>
        ) : null}

        {formData.frequency_type === "MONTHLY_DATE" ? (
          <>
            <Text style={styles.sheetLabel}>Day of month (1–31)</Text>
            <TextInput
              style={[styles.input, { width: 100 }]}
              keyboardType="numeric"
              value={formData.dom_day ? String(formData.dom_day) : ""}
              onChangeText={(v) => setFormData((s) => ({ ...s, dom_day: parseInt(v, 10) || undefined }))}
            />
          </>
        ) : null}

        {formData.frequency_type === "YEARLY_DATE" ? (
          <>
            <Text style={styles.sheetLabel}>Month</Text>
            <View style={styles.chipWrap}>
              {MONTHS.map((m, idx) => {
                const active = formData.doy_month === idx + 1;
                const p = chipPair(active, PURPLE);
                return (
                  <TouchableOpacity key={m} style={p.box as any} onPress={() => setFormData((s) => ({ ...s, doy_month: idx + 1 }))}>
                    <Text style={p.text as any}>{m}</Text>
                  </TouchableOpacity>
                );
              })}
            </View>
            <Text style={styles.sheetLabel}>Day</Text>
            <TextInput
              style={[styles.input, { width: 100 }]}
              keyboardType="numeric"
              value={formData.doy_day ? String(formData.doy_day) : ""}
              onChangeText={(v) => setFormData((s) => ({ ...s, doy_day: parseInt(v, 10) || undefined }))}
            />
          </>
        ) : null}

        {formData.frequency_type === "NTH_WEEKDAY_MONTH" || formData.frequency_type === "NTH_WEEKDAY_QUARTER" ? (
          <>
            <Text style={styles.sheetLabel}>Occurrence</Text>
            <View style={styles.chipWrap}>
              {NTHS.map((n) => {
                const active = formData.nth === n.v;
                const p = chipPair(active, YELLOW);
                return (
                  <TouchableOpacity key={n.v} style={p.box as any} onPress={() => setFormData((s) => ({ ...s, nth: n.v }))}>
                    <Text style={p.text as any}>{n.l}</Text>
                  </TouchableOpacity>
                );
              })}
            </View>
            <Text style={styles.sheetLabel}>Weekday</Text>
            <View style={styles.chipWrap}>
              {DOW.map((d) => {
                const active = formData.nth_weekday === d.v;
                const p = chipPair(active, YELLOW);
                return (
                  <TouchableOpacity key={d.v} style={p.box as any} onPress={() => setFormData((s) => ({ ...s, nth_weekday: d.v }))}>
                    <Text style={p.text as any}>{d.l}</Text>
                  </TouchableOpacity>
                );
              })}
            </View>
          </>
        ) : null}

        <Text style={styles.sheetLabel}>Recurring</Text>
        <View style={styles.rowChips}>
          {[{ v: true, l: "Yes — auto-repeat" }, { v: false, l: "One time" }].map((r) => {
            const active = formData.is_recurring === r.v;
            const p = chipPair(active, TEAL);
            return (
              <TouchableOpacity key={String(r.v)} style={p.box as any} onPress={() => setFormData((s) => ({ ...s, is_recurring: r.v }))}>
                <Text style={p.text as any}>{r.l}</Text>
              </TouchableOpacity>
            );
          })}
        </View>

        <Text style={styles.sheetLabel}>Due</Text>
        <View style={styles.rowChips}>
          {[{ v: "ANYTIME" as const, l: "Anytime" }, { v: "FIXED_TIME" as const, l: "Fixed time" }].map((d) => {
            const active = formData.due_time_mode === d.v;
            const p = chipPair(active, TEAL);
            return (
              <TouchableOpacity key={d.v} style={p.box as any} onPress={() => setFormData((s) => ({ ...s, due_time_mode: d.v }))}>
                <Text style={p.text as any}>{d.l}</Text>
              </TouchableOpacity>
            );
          })}
        </View>
        {formData.due_time_mode === "FIXED_TIME" ? (
          <TextInput
            style={styles.input}
            placeholder="e.g. 18:00"
            placeholderTextColor="#64748b"
            value={formData.due_time ?? ""}
            onChangeText={(v) => setFormData((s) => ({ ...s, due_time: v }))}
          />
        ) : null}

        <TouchableOpacity style={styles.checkboxRow} onPress={() => setFormData((s) => ({ ...s, evidence_required: !s.evidence_required }))}>
          <View style={[styles.checkbox, formData.evidence_required && styles.checkboxChecked]} />
          <Text style={styles.checkboxLabel}>Require photo/file evidence</Text>
        </TouchableOpacity>

        <Text style={styles.sheetLabel}>Assign to</Text>
        <View style={styles.rowChips}>
          {[{ v: "EMPLOYEE" as const, l: "Employee" }, { v: "ROLE" as const, l: "Role & Department" }].map((m) => {
            const active = formData.assignMode === m.v;
            const p = chipPair(active, INDIGO);
            return (
              <TouchableOpacity key={m.v} style={p.box as any} onPress={() => setFormData((s) => ({ ...s, assignMode: m.v }))}>
                <Text style={p.text as any}>{m.l}</Text>
              </TouchableOpacity>
            );
          })}
        </View>

        {formData.assignMode === "EMPLOYEE" ? (
          <TouchableOpacity style={styles.pickerField} onPress={() => setEmployeePickerOpen(true)}>
            <Text style={{ fontSize: 14, color: formData.assigned_to_user_id ? "#e2e8f0" : "#64748b" }}>
              {formData.assigned_to_user_id
                ? employeeOptions.find((e) => e.id === formData.assigned_to_user_id)?.name ?? "Select employee…"
                : "Select employee…"}
            </Text>
          </TouchableOpacity>
        ) : (
          <>
            <View style={styles.rowChips}>
              {(["EMPLOYEE", "MANAGER", "ADMIN"] as const).map((r) => {
                const active = formData.assigned_to_role === r;
                const p = chipPair(active, INDIGO);
                return (
                  <TouchableOpacity key={r} style={p.box as any} onPress={() => setFormData((s) => ({ ...s, assigned_to_role: r }))}>
                    <Text style={p.text as any}>{r.charAt(0) + r.slice(1).toLowerCase()}</Text>
                  </TouchableOpacity>
                );
              })}
            </View>
            <Text style={styles.sheetLabel}>Narrow to department (optional)</Text>
            <View style={styles.chipWrap}>
              {(options?.departments ?? [{ id: "", name: "All departments", branch_id: null }]).map((d) => {
                const active = formData.assigned_to_dept_id === d.id || (!formData.assigned_to_dept_id && !d.id);
                const p = chipPair(active, INDIGO);
                return (
                  <TouchableOpacity key={d.id || "all"} style={p.box as any} onPress={() => setFormData((s) => ({ ...s, assigned_to_dept_id: d.id || undefined }))}>
                    <Text style={p.text as any}>{d.name}</Text>
                  </TouchableOpacity>
                );
              })}
            </View>
          </>
        )}

        <View style={styles.sheetActions}>
          <TouchableOpacity style={styles.clearButton} onPress={() => setFormOpen(false)}>
            <Text style={styles.clearButtonText}>Cancel</Text>
          </TouchableOpacity>
          <TouchableOpacity style={[styles.applyButton, saving && { opacity: 0.6 }]} onPress={submitForm} disabled={saving}>
            {saving ? <ActivityIndicator color="#fff" /> : <Text style={styles.applyButtonText}>{editingTemplateId ? "Save Changes" : "Create Checklist"}</Text>}
          </TouchableOpacity>
        </View>
      </BottomSheet>

      <EmployeePicker
        visible={employeePickerOpen}
        title="Assign to employee"
        employees={employeeOptions}
        onSelect={(e) => {
          setFormData((s) => ({ ...s, assigned_to_user_id: e.id }));
          setEmployeePickerOpen(false);
        }}
        onClose={() => setEmployeePickerOpen(false)}
      />

      {/* Detail sheet */}
      <BottomSheet visible={!!detailItem} onClose={() => setDetailItem(null)}>
        {detailItem ? (
          <>
            <View style={styles.rowChips}>
              <View style={[styles.pill, { backgroundColor: STATUS_META[detailItem.status ?? "PENDING"].bg }]}>
                <Text style={[styles.pillText, { color: STATUS_META[detailItem.status ?? "PENDING"].fg }]}>{STATUS_META[detailItem.status ?? "PENDING"].label}</Text>
              </View>
              <View style={[styles.pill, { backgroundColor: FREQ_BUCKET_META[freqBucketOf(detailItem.frequency_type)].bg }]}>
                <Text style={[styles.pillText, { color: FREQ_BUCKET_META[freqBucketOf(detailItem.frequency_type)].fg }]}>{detailItem.frequency_label}</Text>
              </View>
            </View>
            <Text style={styles.detailTitle}>{detailItem.title}</Text>
            <Text style={styles.detailDesc}>{detailItem.description}</Text>

            <View style={styles.detailKV}>
              <View style={styles.detailKVRow}>
                <Text style={styles.detailKVLabel}>Due</Text>
                <Text style={styles.detailKVValue}>{fmtDue(detailItem.due_at)}</Text>
              </View>
              {canManage ? (
                <>
                  <View style={styles.detailKVRow}>
                    <Text style={styles.detailKVLabel}>Assigned to</Text>
                    <Text style={styles.detailKVValue}>{detailItem.employee_name ?? "—"}</Text>
                  </View>
                  <View style={styles.detailKVRow}>
                    <Text style={styles.detailKVLabel}>Department · Branch</Text>
                    <Text style={styles.detailKVValue}>{detailItem.department_name ?? "—"} · {detailItem.branch_name ?? "—"}</Text>
                  </View>
                  <View style={styles.detailKVRow}>
                    <Text style={styles.detailKVLabel}>Compliance</Text>
                    <Text style={[styles.detailKVValue, { color: detailItem.compliance_pct >= 80 ? GREEN : detailItem.compliance_pct >= 50 ? YELLOW : RED }]}>
                      {detailItem.compliance_pct}%
                    </Text>
                  </View>
                </>
              ) : null}
              {detailItem.evidence_required ? (
                <View style={styles.detailKVRow}>
                  <Text style={styles.detailKVLabel}>Evidence</Text>
                  <Text style={[styles.detailKVValue, { color: YELLOW }]}>📎 Required on completion</Text>
                </View>
              ) : null}
            </View>

            {detailItem.status === "DONE" ? (
              <View style={styles.banner}>
                <Text style={styles.bannerTitleGreen}>Completed {fmtDue(detailItem.completed_at)}</Text>
                {detailItem.delay_reason ? <Text style={styles.bannerNote}>{detailItem.delay_reason}</Text> : null}
              </View>
            ) : null}
            {detailItem.status === "FAILED" ? (
              <View style={[styles.banner, { backgroundColor: "rgba(239,68,68,0.1)", borderColor: "rgba(239,68,68,0.25)" }]}>
                <Text style={styles.bannerTitleRed}>Marked as failed</Text>
                {detailItem.failure_note ? <Text style={styles.bannerNote}>{detailItem.failure_note}</Text> : null}
              </View>
            ) : null}

            {detailItem.status === "PENDING" || detailItem.status === "OVERDUE" ? (
              <TouchableOpacity
                style={[styles.completeButton, { backgroundColor: detailItem.status === "OVERDUE" ? AMBER : TEAL }]}
                onPress={() => openComplete(detailItem)}
              >
                <Text style={[styles.completeButtonText, { color: detailItem.status === "OVERDUE" ? "#1c1204" : "#0b0f1a" }]}>
                  {detailItem.status === "OVERDUE" ? "Mark Complete (Overdue)" : "Mark Complete"}
                </Text>
              </TouchableOpacity>
            ) : null}

            <View style={styles.detailActionsRow}>
              {canManage ? (
                <TouchableOpacity style={styles.actionButton} onPress={notifySelected}>
                  <Text style={styles.actionButtonText}>{notifiedId === detailItem.assignment_id ? "Notified ✓" : "🔔 Notify"}</Text>
                </TouchableOpacity>
              ) : null}
              <TouchableOpacity style={styles.actionButton} onPress={() => openHistory(detailItem)}>
                <Text style={styles.actionButtonText}>History</Text>
              </TouchableOpacity>
              {isAdmin ? (
                <>
                  <TouchableOpacity style={styles.actionButton} onPress={() => openEdit(detailItem)}>
                    <Text style={styles.actionButtonText}>Edit</Text>
                  </TouchableOpacity>
                  <TouchableOpacity style={[styles.actionButton, styles.deleteButton]} onPress={deleteSelected}>
                    <Text style={styles.deleteButtonText}>{armedDeleteId === detailItem.template_id ? "Confirm?" : "Delete"}</Text>
                  </TouchableOpacity>
                </>
              ) : null}
            </View>
          </>
        ) : null}
      </BottomSheet>

      {/* Complete sheet */}
      <BottomSheet visible={!!completingItem} onClose={() => setCompletingItem(null)}>
        {completingItem ? (
          <>
            <Text style={styles.sheetTitle}>Complete checklist</Text>
            <Text style={styles.completeSubtitle}>{completingItem.title}</Text>

            {completingItem.evidence_required ? (
              <>
                <Text style={styles.sheetLabel}>Attach evidence *</Text>
                {evidenceUri ? (
                  <View style={styles.evidenceAttached}>
                    <Text style={styles.evidenceAttachedText}>📎 File attached</Text>
                    <TouchableOpacity onPress={() => setEvidenceUri(null)}>
                      <Text style={{ color: "#5eead4", fontSize: 13 }}>✕</Text>
                    </TouchableOpacity>
                  </View>
                ) : (
                  <TouchableOpacity style={styles.evidenceEmpty} onPress={attachEvidence} disabled={uploadingEvidence}>
                    {uploadingEvidence ? <ActivityIndicator color="#94a3b8" /> : <Text style={styles.evidenceEmptyText}>📎 Attach photo / file</Text>}
                  </TouchableOpacity>
                )}
              </>
            ) : null}

            <Text style={styles.sheetLabel}>{completingItem.status === "OVERDUE" ? "Delay reason *" : "Note"}</Text>
            <TextInput
              style={[styles.input, styles.inputMultiline, completingItem.status === "OVERDUE" && { borderColor: "rgba(245,158,11,0.3)" }]}
              placeholder={completingItem.status === "OVERDUE" ? "Explain why this was completed late…" : "Optional note…"}
              placeholderTextColor="#64748b"
              value={completeNote}
              onChangeText={setCompleteNote}
              multiline
            />

            <View style={styles.sheetActions}>
              <TouchableOpacity style={styles.failButton} onPress={markFailed}>
                <Text style={styles.failButtonText}>Mark as Failed</Text>
              </TouchableOpacity>
              <TouchableOpacity style={styles.applyButton} onPress={markComplete}>
                <Text style={styles.applyButtonText}>Confirm Complete</Text>
              </TouchableOpacity>
            </View>
          </>
        ) : null}
      </BottomSheet>

      {/* History sheet */}
      <BottomSheet visible={!!historyItem} onClose={() => setHistoryItem(null)}>
        {historyItem ? (
          <>
            <Text style={styles.sheetTitle}>{historyItem.item.title} — History</Text>
            <Text style={styles.historySubtitle}>{historyItem.item.frequency_label} · {historyItem.data.records.length} recorded occurrences</Text>
            <View style={styles.historyStats}>
              <View style={styles.historyStatCell}>
                <Text style={styles.historyStatLabel}>Completed</Text>
                <Text style={[styles.historyStatValue, { color: GREEN }]}>{historyItem.data.done_count}</Text>
              </View>
              <View style={styles.historyStatCell}>
                <Text style={styles.historyStatLabel}>Failed</Text>
                <Text style={[styles.historyStatValue, { color: RED }]}>{historyItem.data.failed_count}</Text>
              </View>
              <View style={styles.historyStatCell}>
                <Text style={styles.historyStatLabel}>Compliance</Text>
                <Text style={[styles.historyStatValue, { color: historyItem.data.compliance_pct >= 80 ? GREEN : historyItem.data.compliance_pct >= 50 ? YELLOW : RED }]}>
                  {historyItem.data.compliance_pct}%
                </Text>
              </View>
            </View>
            {historyItem.data.records.map((r, idx) => {
              const sm = STATUS_META[r.status] ?? STATUS_META.PENDING;
              return (
                <View key={idx} style={styles.historyRow}>
                  <View>
                    <Text style={styles.historyRowDate}>{fmtDue(r.date)}</Text>
                    {r.note ? <Text style={styles.historyRowNote}>{r.note}</Text> : null}
                  </View>
                  <View style={[styles.pill, { backgroundColor: sm.bg }]}>
                    <Text style={[styles.pillText, { color: sm.fg, fontSize: 10.5 }]}>{sm.label}</Text>
                  </View>
                </View>
              );
            })}
          </>
        ) : null}
      </BottomSheet>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: "#0b0f1a" },
  topBar: { paddingTop: 54, paddingHorizontal: 20, paddingBottom: 10, flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  topBarLeft: { flexDirection: "row", alignItems: "center", gap: 12 },
  topBarRight: { flexDirection: "row", alignItems: "center", gap: 10 },
  iconButton: { width: 34, height: 34, borderRadius: 10, backgroundColor: "#111827", borderWidth: 1, borderColor: "rgba(255,255,255,0.08)", alignItems: "center", justifyContent: "center" },
  backIcon: { fontSize: 16, color: "#cbd5e1" },
  filterIcon: { fontSize: 15, color: "#cbd5e1" },
  title: { fontSize: 17.5, fontWeight: "800", color: "#f1f5f9" },
  addButton: { width: 34, height: 34, borderRadius: 10, backgroundColor: INDIGO, alignItems: "center", justifyContent: "center" },
  addButtonText: { fontSize: 19, fontWeight: "700", color: "#fff", marginTop: -2 },
  badge: { position: "absolute", top: -4, right: -4, minWidth: 15, height: 15, paddingHorizontal: 3, borderRadius: 8, backgroundColor: TEAL, alignItems: "center", justifyContent: "center" },
  badgeText: { fontSize: 9, fontWeight: "800", color: "#0b0f1a" },
  complianceStrip: { marginHorizontal: 20, marginBottom: 12, padding: 12, borderRadius: 12, backgroundColor: "rgba(45,212,191,0.08)", borderWidth: 1, borderColor: "rgba(45,212,191,0.2)", flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  complianceTitle: { fontSize: 12.5, fontWeight: "700", color: "#f1f5f9" },
  complianceSub: { fontSize: 11, color: "#94a3b8", marginTop: 1 },
  compliancePct: { fontSize: 20, fontWeight: "800", color: TEAL },
  viewTabs: { flexDirection: "row", gap: 6, paddingHorizontal: 20, paddingBottom: 10 },
  viewTab: { flex: 1, height: 40, borderRadius: 11, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, backgroundColor: "#111827", borderWidth: 1, borderColor: "rgba(255,255,255,0.08)" },
  viewTabActive: { backgroundColor: "rgba(45,212,191,0.14)", borderColor: "rgba(45,212,191,0.35)" },
  viewTabDanger: { backgroundColor: "rgba(239,68,68,0.14)", borderColor: "rgba(239,68,68,0.35)" },
  viewTabIcon: { fontSize: 13 },
  viewTabLabel: { fontSize: 12.5, fontWeight: "700" },
  viewTabBadge: { fontSize: 10, fontWeight: "800", backgroundColor: "rgba(255,255,255,0.08)", borderRadius: 8, paddingHorizontal: 5, paddingVertical: 1 },
  error: { color: "#f87185", fontSize: 13, marginHorizontal: 20, marginTop: 8 },
  body: { flex: 1 },
  bodyContent: { paddingHorizontal: 20, paddingBottom: 30 },
  itemCard: { backgroundColor: "#111827", borderWidth: 1, borderColor: "rgba(255,255,255,0.08)", borderRadius: 14, padding: 15, marginBottom: 10 },
  itemHeader: { flexDirection: "row", alignItems: "flex-start", gap: 10 },
  itemDot: { width: 8, height: 8, borderRadius: 4, marginTop: 5 },
  itemTitle: { fontSize: 14.5, fontWeight: "700", color: "#f1f5f9" },
  itemMeta: { fontSize: 12.5, color: "#94a3b8", marginTop: 2 },
  itemFooter: { flexDirection: "row", alignItems: "center", gap: 6, flexWrap: "wrap", marginTop: 11 },
  pill: { paddingVertical: 3, paddingHorizontal: 8, borderRadius: 6 },
  pillText: { fontSize: 11, fontWeight: "700" },
  itemDue: { fontSize: 11.5, fontWeight: "600", marginLeft: "auto" },
  emptyWrap: { alignItems: "center", paddingTop: 50 },
  emptyIcon: { fontSize: 30, marginBottom: 10 },
  emptyLabel: { fontSize: 14, fontWeight: "600", color: "#94a3b8" },
  failedPanel: { marginTop: 8, padding: 14, borderRadius: 12, borderWidth: 1, borderColor: "rgba(239,68,68,0.2)", backgroundColor: "rgba(239,68,68,0.06)" },
  failedHeader: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  failedTitle: { fontSize: 13, fontWeight: "700", color: RED },
  failedToggle: { fontSize: 12, color: "#94a3b8" },
  failedRow: { paddingVertical: 10, borderTopWidth: 1, borderColor: "rgba(239,68,68,0.15)" },
  failedItemTitle: { fontSize: 12.5, fontWeight: "700", color: "#e2e8f0" },
  failedItemMeta: { fontSize: 11, color: "#94a3b8", marginTop: 2 },
  failedItemNote: { fontSize: 11, color: "#f87171", marginTop: 2, fontStyle: "italic" },
  sheetTitle: { fontSize: 16, fontWeight: "800", color: "#f1f5f9", marginBottom: 16 },
  sheetLabel: { fontSize: 12, fontWeight: "600", color: "#94a3b8", marginBottom: 7, marginTop: 4 },
  chipWrap: { flexDirection: "row", gap: 7, flexWrap: "wrap", marginBottom: 14 },
  rowChips: { flexDirection: "row", gap: 7, marginBottom: 14 },
  chip: { paddingVertical: 8, paddingHorizontal: 13, borderRadius: 999, borderWidth: 1 },
  chipInactive: { backgroundColor: "#0d1424", borderColor: "rgba(255,255,255,0.1)" },
  chipText: { fontSize: 12.5, fontWeight: "600" },
  input: { width: "100%", height: 46, borderRadius: 12, backgroundColor: "#0d1424", borderWidth: 1, borderColor: "rgba(255,255,255,0.1)", color: "#e2e8f0", fontSize: 14, paddingHorizontal: 14, marginBottom: 4 },
  inputMultiline: { height: 64, paddingTop: 12, textAlignVertical: "top" },
  checkboxRow: { flexDirection: "row", alignItems: "center", gap: 9, marginVertical: 14 },
  checkbox: { width: 20, height: 20, borderRadius: 6, borderWidth: 1.5, borderColor: "rgba(255,255,255,0.2)" },
  checkboxChecked: { backgroundColor: INDIGO, borderColor: INDIGO },
  checkboxLabel: { fontSize: 13.5, color: "#cbd5e1" },
  pickerField: { width: "100%", height: 46, borderRadius: 12, backgroundColor: "#0d1424", borderWidth: 1, borderColor: "rgba(255,255,255,0.1)", justifyContent: "center", paddingHorizontal: 14, marginBottom: 14 },
  sheetActions: { flexDirection: "row", gap: 10, marginTop: 8 },
  clearButton: { flex: 1, height: 50, borderRadius: 12, borderWidth: 1, borderColor: "rgba(255,255,255,0.12)", alignItems: "center", justifyContent: "center" },
  clearButtonText: { fontSize: 14, fontWeight: "700", color: "#cbd5e1" },
  applyButton: { flex: 2, height: 50, borderRadius: 12, backgroundColor: INDIGO, alignItems: "center", justifyContent: "center" },
  applyButtonText: { fontSize: 15, fontWeight: "700", color: "#fff" },
  failButton: { flex: 1, height: 50, borderRadius: 12, borderWidth: 1.5, borderColor: "rgba(239,68,68,0.4)", backgroundColor: "rgba(239,68,68,0.08)", alignItems: "center", justifyContent: "center" },
  failButtonText: { fontSize: 13.5, fontWeight: "700", color: "#f87171" },
  detailTitle: { fontSize: 18, fontWeight: "800", color: "#f1f5f9", marginBottom: 6 },
  detailDesc: { fontSize: 13.5, color: "#94a3b8", lineHeight: 19, marginBottom: 16 },
  detailKV: { borderTopWidth: 1, borderColor: "rgba(255,255,255,0.06)" },
  detailKVRow: { flexDirection: "row", justifyContent: "space-between", paddingVertical: 10, borderBottomWidth: 1, borderColor: "rgba(255,255,255,0.06)" },
  detailKVLabel: { fontSize: 12, fontWeight: "600", color: "#94a3b8" },
  detailKVValue: { fontSize: 12.5, fontWeight: "700", color: "#e2e8f0" },
  banner: { marginTop: 14, padding: 12, borderRadius: 10, backgroundColor: "rgba(34,197,94,0.1)", borderWidth: 1, borderColor: "rgba(34,197,94,0.25)" },
  bannerTitleGreen: { fontSize: 12.5, fontWeight: "700", color: GREEN },
  bannerTitleRed: { fontSize: 12.5, fontWeight: "700", color: RED },
  bannerNote: { fontSize: 12, color: "#94a3b8", marginTop: 4 },
  completeButton: { marginTop: 16, height: 50, borderRadius: 12, alignItems: "center", justifyContent: "center" },
  completeButtonText: { fontSize: 15, fontWeight: "700" },
  detailActionsRow: { flexDirection: "row", gap: 8, marginTop: 10, flexWrap: "wrap" },
  actionButton: { flex: 1, minWidth: 100, height: 44, borderRadius: 12, borderWidth: 1, borderColor: "rgba(255,255,255,0.1)", alignItems: "center", justifyContent: "center" },
  actionButtonText: { fontSize: 13, fontWeight: "700", color: "#94a3b8" },
  deleteButton: { borderColor: "rgba(239,68,68,0.3)" },
  deleteButtonText: { fontSize: 13, fontWeight: "700", color: "#f87171" },
  completeSubtitle: { fontSize: 13, color: "#94a3b8", marginBottom: 16 },
  evidenceAttached: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", height: 46, borderRadius: 10, backgroundColor: "rgba(45,212,191,0.1)", borderWidth: 1, borderColor: "rgba(45,212,191,0.3)", paddingHorizontal: 14, marginBottom: 14 },
  evidenceAttachedText: { fontSize: 13.5, fontWeight: "600", color: TEAL },
  evidenceEmpty: { height: 46, borderRadius: 10, borderWidth: 1, borderColor: "rgba(255,255,255,0.15)", borderStyle: "dashed", alignItems: "center", justifyContent: "center", marginBottom: 14 },
  evidenceEmptyText: { fontSize: 13.5, fontWeight: "600", color: "#94a3b8" },
  historySubtitle: { fontSize: 12, color: "#64748b", marginBottom: 14 },
  historyStats: { flexDirection: "row", gap: 8, marginBottom: 16 },
  historyStatCell: { flex: 1, backgroundColor: "#0d1424", borderWidth: 1, borderColor: "rgba(255,255,255,0.08)", borderRadius: 10, padding: 10, alignItems: "center" },
  historyStatLabel: { fontSize: 10, color: "#64748b" },
  historyStatValue: { fontSize: 18, fontWeight: "800", marginTop: 2 },
  historyRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", paddingVertical: 10, borderTopWidth: 1, borderColor: "rgba(255,255,255,0.06)" },
  historyRowDate: { fontSize: 12.5, fontWeight: "600", color: "#e2e8f0" },
  historyRowNote: { fontSize: 11, color: "#64748b", marginTop: 1 },
});
