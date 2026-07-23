import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  FlatList,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import DateTimePicker from "@react-native-community/datetimepicker";
import * as ImagePicker from "expo-image-picker";

import type { AuthStackParamList } from "../navigation/AuthNavigator";
import { ApiError } from "../api/client";
import { formatIstDate, formatIstDateTime, toIstIsoDate } from "../utils/dateFormat";
import BottomSheet from "../components/BottomSheet";
import EmployeePicker from "../components/EmployeePicker";
import LinkedEntityPicker, { type LinkedEntitySelection } from "../components/LinkedEntityPicker";
import {
  createTicket,
  getLinkedEntityOptions,
  listEmployeeOptions,
  listTickets,
  uploadAttachment,
  type EmployeeOption,
  type LinkedEntityOption,
  type Ticket,
  type TicketPriority,
} from "../api/tickets";

const TEAL = "#2DD4BF";
const INDIGO = "#6657F2";

type Props = NativeStackScreenProps<AuthStackParamList, "Tickets">;

const PRIORITY_META: Record<TicketPriority, { color: string; label: string }> = {
  CRITICAL: { color: "#ef4444", label: "Top Priority" },
  HIGH: { color: "#f59e0b", label: "High" },
  MEDIUM: { color: "#3b82f6", label: "Medium" },
  LOW: { color: "#64748b", label: "Low" },
};

const STATUS_META: Record<string, { bg: string; fg: string; label: string }> = {
  OPEN: { bg: "rgba(148,163,184,0.14)", fg: "#cbd5e1", label: "Open" },
  DONE: { bg: "rgba(34,197,94,0.14)", fg: "#22c55e", label: "Done" },
  CLOSED: { bg: "rgba(148,163,184,0.14)", fg: "#94a3b8", label: "Closed" },
};

type TabKey = "OPEN" | "ACKNOWLEDGED" | "DONE" | "CLOSED";
const TAB_DEFS: { key: TabKey; label: string; test: (t: Ticket) => boolean }[] = [
  { key: "OPEN", label: "Open", test: (t) => t.status === "OPEN" && !t.acknowledged_at },
  { key: "ACKNOWLEDGED", label: "Ack", test: (t) => t.status === "OPEN" && !!t.acknowledged_at },
  { key: "DONE", label: "Done", test: (t) => t.status === "DONE" },
  { key: "CLOSED", label: "Closed", test: (t) => t.status === "CLOSED" },
];

const PRIORITY_OPTIONS: TicketPriority[] = ["LOW", "MEDIUM", "HIGH", "CRITICAL"];
const CATEGORY_OPTIONS = ["NORMAL", "HELP"] as const;

const fmtDue = formatIstDateTime;

function fmtDateShort(d: Date | null): string {
  if (!d) return "Any";
  return formatIstDate(d);
}

function toggleInArray<T>(arr: T[], val: T): T[] {
  return arr.includes(val) ? arr.filter((v) => v !== val) : [...arr, val];
}

export default function TicketsScreen({ navigation, route }: Props) {
  const { user } = route.params;
  const canManage = user.role === "ADMIN" || user.role === "MANAGER";

  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<TabKey>("OPEN");
  const [employees, setEmployees] = useState<EmployeeOption[]>([]);

  // Filters
  const [filterSheetOpen, setFilterSheetOpen] = useState(false);
  const [filterPriority, setFilterPriority] = useState<TicketPriority[]>([]);
  const [filterCategory, setFilterCategory] = useState<string[]>([]);
  const [filterAssigneeIds, setFilterAssigneeIds] = useState<string[]>([]);
  const [filterFrom, setFilterFrom] = useState<Date | null>(null);
  const [filterTo, setFilterTo] = useState<Date | null>(null);
  const [showFromPicker, setShowFromPicker] = useState(false);
  const [showToPicker, setShowToPicker] = useState(false);
  const [filterAssigneePickerOpen, setFilterAssigneePickerOpen] = useState(false);
  const activeFilterCount = filterPriority.length + filterCategory.length + filterAssigneeIds.length + (filterFrom ? 1 : 0) + (filterTo ? 1 : 0);

  // Create sheet
  const [sheetOpen, setSheetOpen] = useState(false);
  const [assigneePickerOpen, setAssigneePickerOpen] = useState(false);
  const [linkPickerOpen, setLinkPickerOpen] = useState(false);
  const [linkOptions, setLinkOptions] = useState<LinkedEntityOption[]>([]);
  const [links, setLinks] = useState<LinkedEntitySelection[]>([]);
  const [category, setCategory] = useState<"NORMAL" | "HELP">("NORMAL");
  const [title, setTitle] = useState("");
  const [desc, setDesc] = useState("");
  const [priority, setPriority] = useState<TicketPriority>("MEDIUM");
  const [dueDate, setDueDate] = useState(new Date());
  const [showDuePicker, setShowDuePicker] = useState(false);
  const [assignee, setAssignee] = useState<EmployeeOption | null>(null);
  const [evidenceRequired, setEvidenceRequired] = useState(false);
  const [attachmentUri, setAttachmentUri] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Fetches every status so all four tab counts stay visible at once
  // (mirrors the desktop status-tab badges), then each tab filters locally.
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [open, acked, done, closed] = await Promise.all(
        (["OPEN", "ACKNOWLEDGED", "DONE", "CLOSED"] as const).map((status) =>
          listTickets({
            status,
            priority: filterPriority,
            ticketCategory: filterCategory as ("NORMAL" | "HELP")[],
            assigneeId: filterAssigneeIds,
            dateFrom: filterFrom ? toIstIsoDate(filterFrom) : undefined,
            dateTo: filterTo ? toIstIsoDate(filterTo) : undefined,
            limit: 100,
          })
        )
      );
      setTickets([...open.items, ...acked.items, ...done.items, ...closed.items]);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Couldn't load tickets.");
    } finally {
      setLoading(false);
    }
  }, [filterPriority, filterCategory, filterAssigneeIds, filterFrom, filterTo]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (canManage && employees.length === 0) {
      listEmployeeOptions().then(setEmployees).catch(() => {});
    }
  }, [canManage, employees.length]);

  useEffect(() => {
    if (linkPickerOpen && linkOptions.length === 0) {
      getLinkedEntityOptions().then(setLinkOptions).catch(() => {});
    }
  }, [linkPickerOpen, linkOptions.length]);

  const openSheet = () => {
    setTitle("");
    setDesc("");
    setPriority("MEDIUM");
    setDueDate(new Date());
    setAssignee(null);
    setEvidenceRequired(false);
    setCategory("NORMAL");
    setLinks([]);
    setAttachmentUri(null);
    setSheetOpen(true);
  };

  const canSubmit = !!title.trim() && !!desc.trim() && (!canManage || !!assignee);

  const pickAttachment = async (fromCamera: boolean) => {
    const perm = fromCamera
      ? await ImagePicker.requestCameraPermissionsAsync()
      : await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!perm.granted) {
      Alert.alert(fromCamera ? "Camera access needed" : "Photo access needed", "Enable access to attach a document.");
      return;
    }
    const result = fromCamera
      ? await ImagePicker.launchCameraAsync({ quality: 0.6 })
      : await ImagePicker.launchImageLibraryAsync({ quality: 0.6 });
    if (result.canceled || !result.assets?.[0]) return;
    setAttachmentUri(result.assets[0].uri);
  };

  const submit = async () => {
    if (!canSubmit || submitting) return;
    setSubmitting(true);
    try {
      const created = await createTicket({
        title: title.trim(),
        description: desc.trim(),
        priority,
        assigneeId: canManage ? assignee!.id : user.id,
        dueAt: dueDate.toISOString(),
        evidenceRequired,
        ticketCategory: canManage ? category : "HELP",
        linkedEntities: links,
      });
      if (attachmentUri) {
        await uploadAttachment(created.id, attachmentUri, "attachment.jpg");
      }
      setSheetOpen(false);
      await load();
    } catch (e) {
      Alert.alert("Couldn't create ticket", e instanceof ApiError ? e.detail : "Something went wrong.");
    } finally {
      setSubmitting(false);
    }
  };

  const clearFilters = () => {
    setFilterPriority([]);
    setFilterCategory([]);
    setFilterAssigneeIds([]);
    setFilterFrom(null);
    setFilterTo(null);
  };

  const tabCounts = useMemo(() => {
    const counts: Record<TabKey, number> = { OPEN: 0, ACKNOWLEDGED: 0, DONE: 0, CLOSED: 0 };
    TAB_DEFS.forEach((d) => { counts[d.key] = tickets.filter(d.test).length; });
    return counts;
  }, [tickets]);

  const activeTabDef = TAB_DEFS.find((d) => d.key === tab)!;
  const visibleTickets = useMemo(() => tickets.filter(activeTabDef.test), [tickets, activeTabDef]);

  return (
    <View style={styles.screen}>
      <View style={styles.topBar}>
        <View style={styles.topBarLeft}>
          <TouchableOpacity style={styles.iconButton} onPress={() => navigation.goBack()}>
            <Text style={styles.iconButtonText}>‹</Text>
          </TouchableOpacity>
          <Text style={styles.title}>Tickets</Text>
        </View>
        <View style={styles.topBarRight}>
          <TouchableOpacity style={styles.iconButton} onPress={() => setFilterSheetOpen(true)}>
            <Text style={styles.iconButtonText}>▤</Text>
            {activeFilterCount > 0 ? (
              <View style={styles.filterBadge}><Text style={styles.filterBadgeText}>{activeFilterCount}</Text></View>
            ) : null}
          </TouchableOpacity>
          <TouchableOpacity style={styles.addButton} onPress={openSheet}>
            <Text style={styles.addButtonText}>+</Text>
          </TouchableOpacity>
        </View>
      </View>

      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.tabsRow} contentContainerStyle={{ gap: 6 }}>
        {TAB_DEFS.map((d) => {
          const active = tab === d.key;
          return (
            <TouchableOpacity key={d.key} style={[styles.tabChip, active && styles.tabChipActive]} onPress={() => setTab(d.key)}>
              <Text style={[styles.tabChipText, active && styles.tabChipTextActive]}>
                {d.label} {tabCounts[d.key]}
              </Text>
            </TouchableOpacity>
          );
        })}
      </ScrollView>

      {loading ? <ActivityIndicator color={TEAL} style={{ marginTop: 24 }} /> : null}
      {error ? <Text style={styles.error}>{error}</Text> : null}

      <FlatList
        style={styles.list}
        contentContainerStyle={{ padding: 20, paddingTop: 8, paddingBottom: 30 }}
        data={visibleTickets}
        keyExtractor={(t) => t.id}
        ListEmptyComponent={
          !loading ? (
            <View style={styles.emptyState}>
              <Text style={styles.emptyIcon}>🎫</Text>
              <Text style={styles.emptyText}>No tickets in this view.</Text>
            </View>
          ) : null
        }
        renderItem={({ item }) => {
          const overdue = !!item.due_at && new Date(item.due_at) < new Date() && item.status === "OPEN";
          return (
            <TouchableOpacity
              style={styles.card}
              onPress={() => navigation.navigate("TicketDetail", { user, ticketId: item.id })}
            >
              <View style={styles.cardTop}>
                <View style={[styles.priorityDot, { backgroundColor: PRIORITY_META[item.priority].color }]} />
                <View style={{ flex: 1 }}>
                  <Text style={styles.cardTitle}>{item.title}</Text>
                  <Text style={styles.cardMeta}>{item.display_id} · {item.assignee_name ?? "Unassigned"}</Text>
                </View>
              </View>
              <View style={styles.cardBadges}>
                <View style={[styles.badge, { backgroundColor: STATUS_META[item.status].bg }]}>
                  <Text style={[styles.badgeText, { color: STATUS_META[item.status].fg }]}>{STATUS_META[item.status].label}</Text>
                </View>
                {item.ticket_category === "HELP" ? (
                  <View style={[styles.badge, styles.helpBadge]}><Text style={[styles.badgeText, { color: "#eab308" }]}>HELP</Text></View>
                ) : null}
                {item.acknowledged_at ? (
                  <View style={[styles.badge, styles.ackedBadge]}><Text style={[styles.badgeText, { color: "#22c55e" }]}>✓ Acked</Text></View>
                ) : null}
                {item.is_flagged ? <Text style={{ fontSize: 12 }}>🚩</Text> : null}
                <Text style={[styles.dueText, overdue && styles.dueOverdue]}>
                  {fmtDue(item.due_at)}{overdue ? " · Overdue" : ""}
                </Text>
              </View>
            </TouchableOpacity>
          );
        }}
      />

      {/* ── Filters sheet ─────────────────────────────────────────────── */}
      <BottomSheet visible={filterSheetOpen} onClose={() => setFilterSheetOpen(false)}>
        <Text style={styles.modalTitle}>Filter Tickets</Text>

        <Text style={styles.label}>Priority</Text>
        <View style={styles.chipRow}>
          {PRIORITY_OPTIONS.map((p) => {
            const active = filterPriority.includes(p);
            return (
              <TouchableOpacity
                key={p}
                style={[styles.priorityChip, active && { borderColor: PRIORITY_META[p].color, backgroundColor: PRIORITY_META[p].color + "26" }]}
                onPress={() => setFilterPriority((prev) => toggleInArray(prev, p))}
              >
                <Text style={[styles.priorityChipText, active && { color: PRIORITY_META[p].color }]}>{PRIORITY_META[p].label}</Text>
              </TouchableOpacity>
            );
          })}
        </View>

        <Text style={styles.label}>Category</Text>
        <View style={styles.chipRow}>
          {CATEGORY_OPTIONS.map((c) => {
            const active = filterCategory.includes(c);
            return (
              <TouchableOpacity
                key={c}
                style={[styles.categoryChip, active && styles.categoryChipActive]}
                onPress={() => setFilterCategory((prev) => toggleInArray(prev, c))}
              >
                <Text style={[styles.categoryChipText, active && styles.categoryChipTextActive]}>{c === "NORMAL" ? "Normal" : "Help"}</Text>
              </TouchableOpacity>
            );
          })}
        </View>

        {canManage ? (
          <>
            <Text style={styles.label}>Assignee</Text>
            <TouchableOpacity style={styles.input} onPress={() => setFilterAssigneePickerOpen(true)}>
              <Text style={{ color: filterAssigneeIds.length ? "#e2e8f0" : "#64748b" }}>
                {filterAssigneeIds.length
                  ? employees.filter((e) => filterAssigneeIds.includes(e.id)).map((e) => e.name).join(", ")
                  : "Anyone"}
              </Text>
            </TouchableOpacity>
          </>
        ) : null}

        <Text style={styles.label}>Created between</Text>
        <View style={styles.row}>
          <TouchableOpacity style={[styles.input, { flex: 1 }]} onPress={() => setShowFromPicker(true)}>
            <Text style={{ color: filterFrom ? "#e2e8f0" : "#64748b" }}>{fmtDateShort(filterFrom)}</Text>
          </TouchableOpacity>
          <Text style={{ color: "#64748b", alignSelf: "center" }}>→</Text>
          <TouchableOpacity style={[styles.input, { flex: 1 }]} onPress={() => setShowToPicker(true)}>
            <Text style={{ color: filterTo ? "#e2e8f0" : "#64748b" }}>{fmtDateShort(filterTo)}</Text>
          </TouchableOpacity>
        </View>
        {showFromPicker ? (
          <DateTimePicker value={filterFrom ?? new Date()} mode="date" onChange={(_, d) => { setShowFromPicker(false); if (d) setFilterFrom(d); }} />
        ) : null}
        {showToPicker ? (
          <DateTimePicker value={filterTo ?? new Date()} mode="date" onChange={(_, d) => { setShowToPicker(false); if (d) setFilterTo(d); }} />
        ) : null}

        <View style={styles.modalActions}>
          <TouchableOpacity style={styles.modalCancel} onPress={clearFilters}>
            <Text style={styles.modalCancelText}>Clear</Text>
          </TouchableOpacity>
          <TouchableOpacity style={styles.modalSubmit} onPress={() => setFilterSheetOpen(false)}>
            <Text style={styles.modalSubmitText}>Apply</Text>
          </TouchableOpacity>
        </View>
      </BottomSheet>

      {/* ── Create ticket sheet ───────────────────────────────────────── */}
      <BottomSheet visible={sheetOpen} onClose={() => setSheetOpen(false)}>
        <Text style={styles.modalTitle}>{canManage ? "New Ticket" : "Raise Help Ticket"}</Text>

        {canManage ? (
          <View style={styles.chipRow}>
            {CATEGORY_OPTIONS.map((c) => (
              <TouchableOpacity
                key={c}
                style={[styles.categoryChip, category === c && styles.categoryChipActive]}
                onPress={() => setCategory(c)}
              >
                <Text style={[styles.categoryChipText, category === c && styles.categoryChipTextActive]}>
                  {c === "NORMAL" ? "Normal" : "Help"}
                </Text>
              </TouchableOpacity>
            ))}
          </View>
        ) : null}

        <Text style={styles.label}>Title *</Text>
        <TextInput style={styles.input} placeholder="e.g. Fix machine #3 oil leak" placeholderTextColor="#64748b" value={title} onChangeText={setTitle} />

        <Text style={styles.label}>Description *</Text>
        <TextInput
          style={[styles.input, { height: 70, textAlignVertical: "top" }]}
          placeholder="Describe the task…"
          placeholderTextColor="#64748b"
          value={desc}
          onChangeText={setDesc}
          multiline
        />

        <Text style={styles.label}>Priority</Text>
        <View style={styles.chipRow}>
          {PRIORITY_OPTIONS.map((p) => (
            <TouchableOpacity
              key={p}
              style={[styles.priorityChip, priority === p && { borderColor: PRIORITY_META[p].color, backgroundColor: PRIORITY_META[p].color + "26" }]}
              onPress={() => setPriority(p)}
            >
              <Text style={[styles.priorityChipText, priority === p && { color: PRIORITY_META[p].color }]}>{PRIORITY_META[p].label}</Text>
            </TouchableOpacity>
          ))}
        </View>

        <Text style={styles.label}>Due Date *</Text>
        <TouchableOpacity style={styles.input} onPress={() => setShowDuePicker(true)}>
          <Text style={{ color: "#e2e8f0" }}>{fmtDue(dueDate.toISOString())}</Text>
        </TouchableOpacity>
        {showDuePicker ? (
          <DateTimePicker
            value={dueDate}
            mode="datetime"
            onChange={(_, d) => { setShowDuePicker(false); if (d) setDueDate(d); }}
          />
        ) : null}

        {canManage ? (
          <>
            <Text style={styles.label}>Assign To *</Text>
            <TouchableOpacity style={styles.input} onPress={() => setAssigneePickerOpen(true)}>
              <Text style={{ color: assignee ? "#e2e8f0" : "#64748b" }}>{assignee ? assignee.name : "Select employee…"}</Text>
            </TouchableOpacity>
          </>
        ) : null}

        <TouchableOpacity style={styles.checkboxRow} onPress={() => setEvidenceRequired((v) => !v)}>
          <View style={[styles.checkbox, evidenceRequired && styles.checkboxChecked]} />
          <Text style={styles.checkboxLabel}>Evidence required before completion</Text>
        </TouchableOpacity>

        <Text style={styles.label}>Attach document</Text>
        {attachmentUri ? (
          <View style={styles.attachmentPreviewRow}>
            <Text style={styles.attachmentPreviewText} numberOfLines={1}>📎 Photo attached</Text>
            <TouchableOpacity onPress={() => setAttachmentUri(null)}>
              <Text style={styles.linkChipRemove}>✕</Text>
            </TouchableOpacity>
          </View>
        ) : (
          <TouchableOpacity
            style={styles.linkAddButton}
            onPress={() =>
              Alert.alert("Attach a document", "Attach a photo of a supporting document.", [
                { text: "Take Photo", onPress: () => pickAttachment(true) },
                { text: "Choose from Library", onPress: () => pickAttachment(false) },
                { text: "Cancel", style: "cancel" },
              ])
            }
          >
            <Text style={styles.linkAddButtonText}>📎 Attach document (photo)</Text>
          </TouchableOpacity>
        )}

        <Text style={styles.label}>Linked records</Text>
        {links.length > 0 ? (
          <View style={styles.linkChips}>
            {links.map((l, idx) => (
              <View key={idx} style={styles.linkChip}>
                <Text style={styles.linkChipText}>{l.entityLabel}</Text>
                <TouchableOpacity onPress={() => setLinks((prev) => prev.filter((_, i) => i !== idx))}>
                  <Text style={styles.linkChipRemove}>✕</Text>
                </TouchableOpacity>
              </View>
            ))}
          </View>
        ) : null}
        <TouchableOpacity style={styles.linkAddButton} onPress={() => setLinkPickerOpen(true)}>
          <Text style={styles.linkAddButtonText}>🔗 Link a Setup record</Text>
        </TouchableOpacity>

        <View style={styles.modalActions}>
          <TouchableOpacity style={styles.modalCancel} onPress={() => setSheetOpen(false)}>
            <Text style={styles.modalCancelText}>Cancel</Text>
          </TouchableOpacity>
          <TouchableOpacity style={[styles.modalSubmit, !canSubmit && { opacity: 0.5 }]} onPress={submit} disabled={!canSubmit || submitting}>
            {submitting ? <ActivityIndicator color="#fff" /> : <Text style={styles.modalSubmitText}>Create Ticket</Text>}
          </TouchableOpacity>
        </View>
      </BottomSheet>

      <EmployeePicker
        visible={assigneePickerOpen}
        title="Assign To"
        employees={employees}
        onSelect={(e) => { setAssignee(e); setAssigneePickerOpen(false); }}
        onClose={() => setAssigneePickerOpen(false)}
      />

      <EmployeePicker
        visible={filterAssigneePickerOpen}
        title="Filter by Assignee"
        employees={employees}
        multiSelect
        selectedIds={filterAssigneeIds}
        onToggle={(e) => setFilterAssigneeIds((prev) => toggleInArray(prev, e.id))}
        onDone={() => setFilterAssigneePickerOpen(false)}
        onClose={() => setFilterAssigneePickerOpen(false)}
      />

      <LinkedEntityPicker
        visible={linkPickerOpen}
        options={linkOptions}
        onSelect={(sel) => setLinks((prev) => [...prev, sel])}
        onClose={() => setLinkPickerOpen(false)}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: "#0b0f1a" },
  topBar: {
    paddingTop: 58, paddingHorizontal: 20, paddingBottom: 10,
    flexDirection: "row", alignItems: "center", justifyContent: "space-between",
  },
  topBarLeft: { flexDirection: "row", alignItems: "center", gap: 12 },
  topBarRight: { flexDirection: "row", alignItems: "center", gap: 10 },
  iconButton: {
    width: 34, height: 34, borderRadius: 10, backgroundColor: "#111827",
    borderWidth: 1, borderColor: "rgba(255,255,255,0.08)", alignItems: "center", justifyContent: "center",
  },
  iconButtonText: { fontSize: 16, color: "#cbd5e1" },
  filterBadge: {
    position: "absolute", top: -4, right: -4, minWidth: 15, height: 15, paddingHorizontal: 3,
    borderRadius: 8, backgroundColor: TEAL, alignItems: "center", justifyContent: "center",
  },
  filterBadgeText: { fontSize: 9, fontWeight: "800", color: "#0b0f1a" },
  title: { fontSize: 17.5, fontWeight: "800", color: "#f1f5f9" },
  addButton: {
    width: 34, height: 34, borderRadius: 10, alignItems: "center", justifyContent: "center",
    backgroundColor: INDIGO,
  },
  addButtonText: { fontSize: 19, fontWeight: "700", color: "#fff" },
  tabsRow: { paddingHorizontal: 20, paddingBottom: 4, flexGrow: 0 },
  tabChip: {
    paddingHorizontal: 12, paddingVertical: 7, borderRadius: 999,
    backgroundColor: "#111827", borderWidth: 1, borderColor: "rgba(255,255,255,0.08)",
  },
  tabChipActive: { backgroundColor: "rgba(45,212,191,0.14)", borderColor: "rgba(45,212,191,0.35)" },
  tabChipText: { fontSize: 13, fontWeight: "600", color: "#94a3b8" },
  tabChipTextActive: { color: TEAL },
  error: { color: "#f87185", fontSize: 13, marginHorizontal: 20, marginTop: 8 },
  list: { flex: 1 },
  emptyState: { alignItems: "center", paddingTop: 60 },
  emptyIcon: { fontSize: 30, marginBottom: 10 },
  emptyText: { fontSize: 14, fontWeight: "600", color: "#94a3b8" },
  card: {
    backgroundColor: "#111827", borderWidth: 1, borderColor: "rgba(255,255,255,0.08)",
    borderRadius: 14, padding: 15, marginBottom: 10,
  },
  cardTop: { flexDirection: "row", alignItems: "flex-start", gap: 10 },
  priorityDot: { width: 8, height: 8, borderRadius: 4, marginTop: 5 },
  cardTitle: { fontSize: 14.5, fontWeight: "700", color: "#f1f5f9" },
  cardMeta: { fontSize: 12.5, color: "#94a3b8", marginTop: 2 },
  cardBadges: { flexDirection: "row", alignItems: "center", gap: 6, flexWrap: "wrap", marginTop: 10 },
  badge: { paddingHorizontal: 8, paddingVertical: 3, borderRadius: 6 },
  badgeText: { fontSize: 11, fontWeight: "700" },
  helpBadge: { backgroundColor: "rgba(234,179,8,0.16)" },
  ackedBadge: { backgroundColor: "rgba(34,197,94,0.16)" },
  dueText: { fontSize: 11.5, fontWeight: "600", color: "#64748b", marginLeft: "auto" },
  dueOverdue: { color: "#fb7185" },
  modalTitle: { fontSize: 18, fontWeight: "800", color: "#f1f5f9", marginBottom: 16 },
  label: { fontSize: 13, fontWeight: "600", color: "#94a3b8", marginBottom: 6, marginTop: 12 },
  input: {
    width: "100%", height: 48, borderRadius: 12, backgroundColor: "#0d1424",
    borderWidth: 1, borderColor: "rgba(255,255,255,0.1)", color: "#e2e8f0", fontSize: 14.5, paddingHorizontal: 14, justifyContent: "center",
  },
  row: { flexDirection: "row", gap: 10 },
  chipRow: { flexDirection: "row", gap: 7, flexWrap: "wrap" },
  categoryChip: {
    paddingHorizontal: 14, paddingVertical: 8, borderRadius: 10, backgroundColor: "#0d1424",
    borderWidth: 1, borderColor: "rgba(255,255,255,0.1)",
  },
  categoryChipActive: { backgroundColor: "rgba(102,87,242,0.16)", borderColor: "rgba(102,87,242,0.4)" },
  categoryChipText: { fontSize: 13, fontWeight: "600", color: "#94a3b8" },
  categoryChipTextActive: { color: "#a99cf7" },
  priorityChip: {
    paddingHorizontal: 10, paddingVertical: 7, borderRadius: 8, backgroundColor: "#0d1424",
    borderWidth: 1, borderColor: "rgba(255,255,255,0.1)",
  },
  priorityChipText: { fontSize: 12, fontWeight: "600", color: "#94a3b8" },
  checkboxRow: { flexDirection: "row", alignItems: "center", gap: 8, marginTop: 16 },
  checkbox: { width: 18, height: 18, borderRadius: 5, borderWidth: 1.5, borderColor: "rgba(255,255,255,0.25)" },
  checkboxChecked: { backgroundColor: TEAL, borderColor: TEAL },
  checkboxLabel: { fontSize: 13.5, color: "#cbd5e1" },
  linkChips: { flexDirection: "row", flexWrap: "wrap", gap: 6, marginBottom: 8 },
  linkChip: {
    flexDirection: "row", alignItems: "center", gap: 6, paddingHorizontal: 10, paddingVertical: 6, borderRadius: 999,
    backgroundColor: "rgba(59,130,246,0.12)", borderWidth: 1, borderColor: "rgba(59,130,246,0.3)",
  },
  linkChipText: { fontSize: 12, color: "#93c5fd", fontWeight: "600" },
  linkChipRemove: { fontSize: 13, color: "#60a5fa" },
  linkAddButton: {
    height: 44, borderRadius: 10, borderWidth: 1, borderColor: "rgba(255,255,255,0.12)", borderStyle: "dashed",
    alignItems: "center", justifyContent: "center",
  },
  linkAddButtonText: { fontSize: 13.5, fontWeight: "600", color: "#94a3b8" },
  attachmentPreviewRow: {
    flexDirection: "row", alignItems: "center", justifyContent: "space-between",
    height: 44, borderRadius: 10, backgroundColor: "rgba(45,212,191,0.1)", borderWidth: 1, borderColor: "rgba(45,212,191,0.3)",
    paddingHorizontal: 14,
  },
  attachmentPreviewText: { fontSize: 13.5, fontWeight: "600", color: TEAL, flex: 1 },
  modalActions: { flexDirection: "row", gap: 10, marginTop: 22 },
  modalCancel: {
    flex: 1, height: 50, borderRadius: 12, borderWidth: 1, borderColor: "rgba(255,255,255,0.12)",
    alignItems: "center", justifyContent: "center",
  },
  modalCancelText: { fontSize: 14, fontWeight: "700", color: "#cbd5e1" },
  modalSubmit: { flex: 2, height: 50, borderRadius: 12, backgroundColor: INDIGO, alignItems: "center", justifyContent: "center" },
  modalSubmitText: { fontSize: 15, fontWeight: "700", color: "#fff" },
});
