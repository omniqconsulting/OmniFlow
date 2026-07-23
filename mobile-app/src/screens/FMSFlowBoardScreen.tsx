import { useCallback, useEffect, useMemo, useState } from "react";
import { ActivityIndicator, Alert, ScrollView, StyleSheet, Text, TextInput, TouchableOpacity, View } from "react-native";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import * as ImagePicker from "expo-image-picker";

import type { AuthStackParamList } from "../navigation/AuthNavigator";
import { ApiError } from "../api/client";
import {
  bulkTransition,
  createTicket,
  getAssignableEmployees,
  getBoard,
  getFlows,
  getTicket,
  getTicketEvents,
  requestHelp,
  ticketAction,
  transitionTicket,
  type FMSBoard,
  type FMSEmployee,
  type FMSFlow,
  type FMSStage,
  type FMSSubAction,
  type FMSTicket,
  type FMSTicketDetail,
  type FMSTicketEvent,
} from "../api/fms";
import type { EmployeeOption } from "../api/tickets";
import BottomSheet from "../components/BottomSheet";
import EmployeePicker from "../components/EmployeePicker";

const TEAL = "#2DD4BF";
const INDIGO = "#6657F2";
const RED = "#ef4444";
const AMBER = "#f59e0b";
const GREEN = "#22c55e";
const BLUE = "#3b82f6";
const PURPLE = "#8b5cf6";
const YELLOW = "#eab308";
const GRAY_FG = "#94a3b8";
const GRAY_BG = "rgba(148,163,184,0.14)";

type Props = NativeStackScreenProps<AuthStackParamList, "FMSFlowBoard">;

const PRIORITY_META: Record<string, { fg: string; bg: string; label: string }> = {
  LOW: { fg: GRAY_FG, bg: GRAY_BG, label: "Low" },
  MEDIUM: { fg: BLUE, bg: "rgba(59,130,246,0.14)", label: "Medium" },
  HIGH: { fg: AMBER, bg: "rgba(245,158,11,0.14)", label: "High" },
  CRITICAL: { fg: RED, bg: "rgba(239,68,68,0.14)", label: "Critical" },
};
const STATUS_META: Record<string, { fg: string; bg: string; label: string }> = {
  ACTIVE: { fg: BLUE, bg: "rgba(59,130,246,0.14)", label: "Active" },
  STAGE_COMPLETE: { fg: TEAL, bg: "rgba(45,212,191,0.14)", label: "Stage Complete" },
  IN_TRANSITION: { fg: PURPLE, bg: "rgba(139,92,246,0.14)", label: "In Transition" },
  ON_HOLD: { fg: GRAY_FG, bg: GRAY_BG, label: "On Hold" },
  HELP_REQUESTED: { fg: "#f97316", bg: "rgba(249,115,22,0.14)", label: "Help Needed" },
  FLAGGED: { fg: RED, bg: "rgba(239,68,68,0.14)", label: "Flagged" },
  COMPLETED: { fg: GREEN, bg: "rgba(34,197,94,0.14)", label: "Completed" },
  CLOSED: { fg: GRAY_FG, bg: GRAY_BG, label: "Closed" },
};
const PRIORITY_OPTIONS = ["LOW", "MEDIUM", "HIGH", "CRITICAL"];

function tatColor(pct: number | null): string {
  if (pct == null) return "#64748b";
  return pct < 50 ? GREEN : pct < 90 ? AMBER : RED;
}

const SUB_META: Record<FMSSubAction, { title: string; textLabel: string; placeholder: string; submitLabel: string; needsAssignee: boolean; assigneeLabel?: string }> = {
  comment: { title: "Add Comment", textLabel: "Comment", placeholder: "Write a comment…", submitLabel: "Post", needsAssignee: false },
  flag: { title: "Flag Ticket", textLabel: "Flag reason", placeholder: "e.g. Missed deadline", submitLabel: "Confirm Flag", needsAssignee: false },
  unflag: { title: "Remove Flag", textLabel: "Note", placeholder: "Optional note", submitLabel: "Remove Flag", needsAssignee: false },
  reassign: { title: "Reassign Ticket", textLabel: "Reason", placeholder: "Reason for reassignment", submitLabel: "Confirm Reassign", needsAssignee: true, assigneeLabel: "New assignee" },
  on_hold: { title: "Put On Hold", textLabel: "Reason", placeholder: "Optional reason", submitLabel: "Confirm Hold", needsAssignee: false },
  resume: { title: "Resume Ticket", textLabel: "Note", placeholder: "Optional note", submitLabel: "Resume", needsAssignee: false },
  add_helper: { title: "Add Helper", textLabel: "Reason", placeholder: "Why are they helping?", submitLabel: "Add Helper", needsAssignee: true, assigneeLabel: "Employee" },
  close: { title: "Close Ticket", textLabel: "Reason", placeholder: "Optional reason", submitLabel: "Close Ticket", needsAssignee: false },
  send_to_linked_flow: { title: "Send to Linked Flow", textLabel: "Reason", placeholder: "Optional reason", submitLabel: "Send", needsAssignee: false },
  close_and_continue: { title: "Close & Continue", textLabel: "Reason", placeholder: "Optional reason", submitLabel: "Close & Continue", needsAssignee: false },
};

function fmtDate(iso: string | null): string {
  if (!iso) return "No due date";
  return new Date(iso).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata", day: "numeric", month: "short" });
}

function toggleInArray<T>(arr: T[], val: T): T[] {
  return arr.includes(val) ? arr.filter((v) => v !== val) : [...arr, val];
}

export default function FMSFlowBoardScreen({ navigation, route }: Props) {
  const { user, initialFlowId, initialTicketId } = route.params;
  const canManage = user.role === "ADMIN" || user.role === "MANAGER";

  const [flows, setFlows] = useState<FMSFlow[] | null>(null);
  const [activeFlowId, setActiveFlowId] = useState<string | null>(initialFlowId ?? null);
  const [pendingTicketId, setPendingTicketId] = useState<string | null>(initialTicketId ?? null);
  const [employees, setEmployees] = useState<FMSEmployee[]>([]);
  const [board, setBoard] = useState<FMSBoard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [scopeMyWork, setScopeMyWork] = useState(false);
  const [viewMode, setViewMode] = useState<"stage" | "table">("stage");
  const [selectedStageId, setSelectedStageId] = useState<string | null>(null);
  const [bulkMode, setBulkMode] = useState(false);
  const [selectedTicketIds, setSelectedTicketIds] = useState<string[]>([]);
  const [statusFilter, setStatusFilter] = useState<"" | "ACTIVE" | "OVERDUE" | "CLOSED">("");

  const [flowPickerOpen, setFlowPickerOpen] = useState(false);

  const [createOpen, setCreateOpen] = useState(false);
  const [createFlowId, setCreateFlowId] = useState<string | null>(null);
  const [createStageId, setCreateStageId] = useState<string | null>(null);
  const [createTitle, setCreateTitle] = useState("");
  const [createPriority, setCreatePriority] = useState("MEDIUM");
  const [createDue, setCreateDue] = useState("");
  const [createAssigneeId, setCreateAssigneeId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const [detailTicketId, setDetailTicketId] = useState<string | null>(null);
  const [detail, setDetail] = useState<FMSTicketDetail | null>(null);
  const [detailEvents, setDetailEvents] = useState<FMSTicketEvent[]>([]);
  const [expandedStageRow, setExpandedStageRow] = useState<string | null>(null);

  const [transitionOpen, setTransitionOpen] = useState(false);
  const [transNextStageId, setTransNextStageId] = useState<string | null>(null);
  const [transAssigneeId, setTransAssigneeId] = useState<string | null>(null);
  const [transNote, setTransNote] = useState("");
  const [transReturnReason, setTransReturnReason] = useState("");
  const [transOverride, setTransOverride] = useState(false);
  const [transCustomValues, setTransCustomValues] = useState<Record<string, string>>({});
  const [transEvidenceUri, setTransEvidenceUri] = useState<string | null>(null);
  const [transSubmitting, setTransSubmitting] = useState(false);

  const [actionsOpen, setActionsOpen] = useState(false);
  const [helpSheetOpen, setHelpSheetOpen] = useState(false);
  const [subAction, setSubAction] = useState<FMSSubAction | null>(null);
  const [subAssigneeId, setSubAssigneeId] = useState<string | null>(null);
  const [subText, setSubText] = useState("");
  const [subSubmitting, setSubSubmitting] = useState(false);

  const [employeePickerOpen, setEmployeePickerOpen] = useState(false);
  const [employeePickerTarget, setEmployeePickerTarget] = useState<"create" | "transition" | "sub">("create");

  const employeeOptions: EmployeeOption[] = useMemo(() => employees.map((e) => ({ id: e.id, name: e.name })), [employees]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const fl = await getFlows();
      setFlows(fl);
      const firstFlowId = activeFlowId && fl.some((f) => f.id === activeFlowId) ? activeFlowId : fl[0]?.id ?? null;
      setActiveFlowId(firstFlowId);
      if (firstFlowId) {
        const b = await getBoard({ flowId: firstFlowId, myWork: scopeMyWork });
        setBoard(b);
      }
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Couldn't load the Flow Board.");
    } finally {
      setLoading(false);
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    load();
    getAssignableEmployees().then(setEmployees).catch(() => {});
  }, [load]);

  const reloadBoard = useCallback(async () => {
    if (!activeFlowId) return;
    try {
      const b = await getBoard({ flowId: activeFlowId, myWork: scopeMyWork });
      setBoard(b);
    } catch (e) {
      Alert.alert("Couldn't refresh", e instanceof ApiError ? e.detail : "Something went wrong.");
    }
  }, [activeFlowId, scopeMyWork]);

  useEffect(() => {
    reloadBoard();
  }, [reloadBoard]);

  // Deep-link from My Tasks: once the requested flow's board has loaded,
  // jump straight into that ticket's detail/transition sheet.
  useEffect(() => {
    if (pendingTicketId && board?.tickets.some((t) => t.id === pendingTicketId)) {
      openDetail(pendingTicketId);
      setPendingTicketId(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingTicketId, board]);

  const activeFlow: FMSFlow | null = flows?.find((f) => f.id === activeFlowId) ?? null;
  const stages: FMSStage[] = activeFlow?.stages ?? [];

  useEffect(() => {
    if (stages.length && !stages.some((s) => s.id === selectedStageId)) {
      setSelectedStageId(stages[0].id);
    }
  }, [stages, selectedStageId]);

  const tickets = board?.tickets ?? [];
  const stageCounts = useMemo(() => {
    const m: Record<string, number> = {};
    stages.forEach((s) => { m[s.id] = tickets.filter((t) => t.current_stage_id === s.id).length; });
    return m;
  }, [stages, tickets]);

  const selectedStage = stages.find((s) => s.id === selectedStageId) ?? stages[0] ?? null;
  const stageTickets = selectedStage ? tickets.filter((t) => t.current_stage_id === selectedStage.id) : [];
  const nextStageObj = selectedStage ? stages.find((s) => s.order === selectedStage.order + 1) ?? null : null;
  const canBulkSelect = canManage && !!nextStageObj && stageTickets.length > 0;

  const tableTickets = useMemo(() => {
    return tickets.filter((t) => {
      if (statusFilter === "ACTIVE") return t.status === "ACTIVE";
      if (statusFilter === "OVERDUE") return t.tat_pct != null && t.tat_pct >= 100;
      if (statusFilter === "CLOSED") return t.status === "COMPLETED" || t.status === "CLOSED";
      return true;
    });
  }, [tickets, statusFilter]);

  // ── Detail / events ─────────────────────────────────────────────────────
  const openDetail = async (ticketId: string) => {
    setDetailTicketId(ticketId);
    setExpandedStageRow(null);
    try {
      const [d, ev] = await Promise.all([getTicket(ticketId), getTicketEvents(ticketId)]);
      setDetail(d);
      setDetailEvents(ev);
    } catch (e) {
      Alert.alert("Couldn't load ticket", e instanceof ApiError ? e.detail : "Something went wrong.");
      setDetailTicketId(null);
    }
  };
  const refreshDetail = async (ticketId: string) => {
    try {
      const [d, ev] = await Promise.all([getTicket(ticketId), getTicketEvents(ticketId)]);
      setDetail(d);
      setDetailEvents(ev);
    } catch {
      // stale sheet content is acceptable; next open re-fetches
    }
  };
  const closeDetail = () => {
    setDetailTicketId(null);
    setDetail(null);
    setDetailEvents([]);
  };

  const canTransitionSelected = !!detail && (canManage || detail.current_assignee_id === user.id) && detail.status !== "COMPLETED" && detail.status !== "CLOSED";

  // ── Create ───────────────────────────────────────────────────────────────
  const openCreate = () => {
    setCreateFlowId(activeFlowId ?? flows?.[0]?.id ?? null);
    setCreateStageId((activeFlow ?? flows?.[0])?.stages[0]?.id ?? null);
    setCreateTitle("");
    setCreatePriority("MEDIUM");
    setCreateDue("");
    setCreateAssigneeId(null);
    setCreateOpen(true);
  };
  const createFlowObj = flows?.find((f) => f.id === createFlowId) ?? null;
  const canSubmitCreate = createTitle.trim().length > 0 && !!createAssigneeId && !!createFlowId && !!createStageId;
  const submitCreate = async () => {
    if (!canSubmitCreate || !createFlowId || !createStageId || !createAssigneeId) return;
    setCreating(true);
    try {
      await createTicket({
        flowId: createFlowId, startingStageId: createStageId, title: createTitle.trim(),
        priority: createPriority, assigneeId: createAssigneeId, dueAt: createDue.trim() || undefined,
      });
      setCreateOpen(false);
      if (createFlowId === activeFlowId) await reloadBoard();
      else setActiveFlowId(createFlowId);
    } catch (e) {
      Alert.alert("Couldn't create ticket", e instanceof ApiError ? e.detail : "Something went wrong.");
    } finally {
      setCreating(false);
    }
  };

  // ── Bulk transition ──────────────────────────────────────────────────────
  const toggleBulkSelect = (id: string) => setSelectedTicketIds((prev) => toggleInArray(prev, id));
  const submitBulkTransition = async () => {
    if (!nextStageObj || selectedTicketIds.length === 0) return;
    try {
      const res = await bulkTransition(selectedTicketIds, nextStageObj.id);
      setBulkMode(false);
      setSelectedTicketIds([]);
      await reloadBoard();
      if (res.skipped.length) {
        Alert.alert("Some tickets skipped", res.skipped.map((s) => s.reason).join("\n"));
      }
    } catch (e) {
      Alert.alert("Bulk move failed", e instanceof ApiError ? e.detail : "Something went wrong.");
    }
  };

  // ── Transition ───────────────────────────────────────────────────────────
  const validStages = useMemo(() => {
    if (!detail || !selectedStage) return [];
    const curOrder = detail.current_stage_order ?? 0;
    return stages.filter((s) => s.order === curOrder + 1 || s.order < curOrder);
  }, [detail, stages, selectedStage]);
  const curStageDef = stages.find((s) => s.id === detail?.current_stage_id) ?? null;
  const chosenNextStage = stages.find((s) => s.id === transNextStageId) ?? null;
  const showReturnReason = !!chosenNextStage && !!curStageDef && chosenNextStage.order < curStageDef.order;
  const showEvidence = !!curStageDef?.evidence_required && !showReturnReason && !transOverride;
  const requiredCustomFields = curStageDef?.custom_fields.filter((f) => f.required && f.field_type !== "formula") ?? [];
  const noteRequired = !!curStageDef?.completion_note_required && !showReturnReason && !transOverride;
  const canSubmitTransition =
    !!transNextStageId && !!transAssigneeId &&
    (!showReturnReason || transReturnReason.trim().length >= 5) &&
    (!showEvidence || !!transEvidenceUri) &&
    (!noteRequired || transNote.trim().length > 0) &&
    (showReturnReason || transOverride || requiredCustomFields.every((f) => (transCustomValues[f.id] ?? "").trim().length > 0));

  const openTransition = () => {
    setTransNextStageId(null);
    setTransAssigneeId(null);
    setTransNote("");
    setTransReturnReason("");
    setTransOverride(false);
    setTransCustomValues({});
    setTransEvidenceUri(null);
    setTransitionOpen(true);
  };
  const attachTransitionEvidence = async () => {
    const perm = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!perm.granted) return;
    const result = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ImagePicker.MediaTypeOptions.Images, quality: 0.7 });
    if (result.canceled || !result.assets[0]) return;
    setTransEvidenceUri(result.assets[0].uri);
  };
  const submitTransition = async () => {
    if (!canSubmitTransition || !detailTicketId || !transAssigneeId) return;
    setTransSubmitting(true);
    try {
      await transitionTicket(detailTicketId, {
        nextStageId: transNextStageId ?? undefined,
        newAssigneeId: transAssigneeId,
        completionNote: transNote,
        returnReason: transReturnReason,
        isOverride: transOverride,
        customFieldValues: transCustomValues,
        evidenceUri: transEvidenceUri ?? undefined,
      });
      setTransitionOpen(false);
      closeDetail();
      await reloadBoard();
    } catch (e) {
      Alert.alert("Couldn't move ticket", e instanceof ApiError ? e.detail : "Something went wrong.");
    } finally {
      setTransSubmitting(false);
    }
  };

  // ── Sub-actions ──────────────────────────────────────────────────────────
  const openSubAction = (action: FMSSubAction) => {
    setSubAction(action);
    setSubAssigneeId(null);
    setSubText("");
    setActionsOpen(false);
  };
  const submitSubAction = async () => {
    if (!subAction || !detailTicketId) return;
    const meta = SUB_META[subAction];
    if (meta.needsAssignee && !subAssigneeId) {
      Alert.alert(`${meta.assigneeLabel ?? "Assignee"} is required`);
      return;
    }
    setSubSubmitting(true);
    try {
      if ((subAction === "close" || subAction === "send_to_linked_flow" || subAction === "close_and_continue") && !canManage) throw new ApiError(403, "Managers only");
      if (subAction === "reassign" || subAction === "add_helper") {
        if (!subText.trim() && subAction === "reassign") {
          Alert.alert("A reason is required");
          setSubSubmitting(false);
          return;
        }
      }
      if (subAction === "add_helper") {
        await ticketAction(detailTicketId, { action: "add_helper", helperId: subAssigneeId ?? undefined, reason: subText });
      } else if (subAction === "reassign") {
        await ticketAction(detailTicketId, { action: "reassign", newAssigneeId: subAssigneeId ?? undefined, reason: subText });
      } else if (subAction === "flag") {
        await ticketAction(detailTicketId, { action: "flag", flagReason: subText });
      } else {
        await ticketAction(detailTicketId, { action: subAction, comment: subText, reason: subText });
      }
      setSubAction(null);
      await refreshDetail(detailTicketId);
      await reloadBoard();
    } catch (e) {
      Alert.alert("Couldn't complete action", e instanceof ApiError ? e.detail : "Something went wrong.");
    } finally {
      setSubSubmitting(false);
    }
  };
  const submitHelpRequest = async () => {
    if (!detailTicketId) return;
    if (!subText.trim()) {
      Alert.alert("Please describe the issue");
      return;
    }
    setSubSubmitting(true);
    try {
      await requestHelp(detailTicketId, subText.trim());
      setHelpSheetOpen(false);
      await refreshDetail(detailTicketId);
      await reloadBoard();
    } catch (e) {
      Alert.alert("Couldn't send request", e instanceof ApiError ? e.detail : "Something went wrong.");
    } finally {
      setSubSubmitting(false);
    }
  };

  const chipPair = (active: boolean, color: string) => ({
    box: [styles.chip, active ? { backgroundColor: color + "26", borderColor: color } : styles.chipInactive],
    text: [styles.chipText, { color: active ? color : "#94a3b8" }],
  });

  const openEmployeePicker = (target: "create" | "transition" | "sub") => {
    setEmployeePickerTarget(target);
    setEmployeePickerOpen(true);
  };
  const onEmployeePicked = (e: EmployeeOption) => {
    if (employeePickerTarget === "create") setCreateAssigneeId(e.id);
    else if (employeePickerTarget === "transition") setTransAssigneeId(e.id);
    else setSubAssigneeId(e.id);
    setEmployeePickerOpen(false);
  };

  // ── Render ───────────────────────────────────────────────────────────────
  return (
    <View style={styles.screen}>
      <View style={styles.topBar}>
        <View style={styles.topBarLeft}>
          <TouchableOpacity style={styles.iconButton} onPress={() => navigation.goBack()}>
            <Text style={styles.backIcon}>‹</Text>
          </TouchableOpacity>
          <Text style={styles.title}>Flow Board</Text>
        </View>
        {canManage ? (
          <TouchableOpacity style={styles.addButton} onPress={openCreate}>
            <Text style={styles.addButtonText}>+</Text>
          </TouchableOpacity>
        ) : null}
      </View>

      {activeFlow ? (
        <TouchableOpacity style={styles.flowBar} onPress={() => setFlowPickerOpen(true)}>
          <View style={[styles.flowDot, { backgroundColor: activeFlow.color }]} />
          <Text style={styles.flowBarName}>{activeFlow.name}</Text>
          <Text style={styles.flowBarSub}>{activeFlow.stages.length} stages ▾</Text>
        </TouchableOpacity>
      ) : null}

      {error ? <Text style={styles.error}>{error}</Text> : null}
      {loading && !board ? <ActivityIndicator color={TEAL} style={{ marginTop: 24 }} /> : null}

      {board ? (
        <ScrollView style={styles.body} contentContainerStyle={styles.bodyContent}>
          <View style={styles.kpiGrid}>
            <View style={styles.kpiCard}>
              <Text style={styles.kpiValue}>{board.kpis.active_tickets}</Text>
              <Text style={styles.kpiLabel}>Active Tickets</Text>
            </View>
            <View style={[styles.kpiCard, board.kpis.tat_breaches > 0 && styles.kpiCardBreach]}>
              <Text style={[styles.kpiValue, board.kpis.tat_breaches > 0 && { color: RED }]}>{board.kpis.tat_breaches}</Text>
              <Text style={styles.kpiLabel}>TaT Breaches</Text>
            </View>
            <View style={[styles.kpiCard, board.kpis.flagged > 0 && styles.kpiCardFlag]}>
              <Text style={[styles.kpiValue, board.kpis.flagged > 0 && { color: AMBER }]}>{board.kpis.flagged}</Text>
              <Text style={styles.kpiLabel}>Flagged</Text>
            </View>
            <View style={styles.kpiCard}>
              <Text style={styles.kpiValue}>{board.kpis.awaiting_action}</Text>
              <Text style={styles.kpiLabel}>Awaiting Action</Text>
            </View>
          </View>
          <View style={styles.complianceRow}>
            <Text style={styles.complianceLabel}>Compliance — completed on time</Text>
            <Text style={styles.compliancePct}>{board.kpis.compliance_pct}%</Text>
          </View>

          <View style={styles.toggleRow}>
            <View style={styles.togglePair}>
              <TouchableOpacity style={[styles.toggleHalf, !scopeMyWork && styles.toggleHalfActive]} onPress={() => setScopeMyWork(false)}>
                <Text style={[styles.toggleText, !scopeMyWork && styles.toggleTextActive]}>Everyone</Text>
              </TouchableOpacity>
              <TouchableOpacity style={[styles.toggleHalf, scopeMyWork && styles.toggleHalfActive]} onPress={() => setScopeMyWork(true)}>
                <Text style={[styles.toggleText, scopeMyWork && styles.toggleTextActive]}>My Work</Text>
              </TouchableOpacity>
            </View>
            <View style={styles.togglePair}>
              <TouchableOpacity style={[styles.toggleHalf, viewMode === "stage" && styles.toggleHalfActive]} onPress={() => setViewMode("stage")}>
                <Text style={[styles.toggleText, viewMode === "stage" && styles.toggleTextActive]}>Stage</Text>
              </TouchableOpacity>
              <TouchableOpacity style={[styles.toggleHalf, viewMode === "table" && styles.toggleHalfActive]} onPress={() => setViewMode("table")}>
                <Text style={[styles.toggleText, viewMode === "table" && styles.toggleTextActive]}>Table</Text>
              </TouchableOpacity>
            </View>
          </View>

          {viewMode === "stage" ? (
            <>
              <View style={styles.stageChipRow}>
                {stages.map((s) => {
                  const active = selectedStageId === s.id;
                  return (
                    <TouchableOpacity
                      key={s.id}
                      style={[styles.stageChip, { backgroundColor: active ? s.color + "22" : "#111827", borderColor: active ? s.color : "rgba(255,255,255,0.08)" }]}
                      onPress={() => { setSelectedStageId(s.id); setBulkMode(false); setSelectedTicketIds([]); }}
                    >
                      <View style={[styles.stageChipDot, { backgroundColor: s.color }]} />
                      <Text style={[styles.stageChipText, { color: active ? "#f1f5f9" : "#94a3b8" }]}>{s.name}</Text>
                      <Text style={[styles.stageChipCount, { color: active ? s.color : "#64748b" }]}>{stageCounts[s.id] ?? 0}</Text>
                    </TouchableOpacity>
                  );
                })}
              </View>

              {selectedStage ? (
                <View style={styles.stageSummaryRow}>
                  <Text style={styles.stageSummaryText}>
                    {stageTickets.length} ticket{stageTickets.length === 1 ? "" : "s"}
                    {selectedStage.target_tat_hours ? ` · TaT ${selectedStage.target_tat_hours}h target` : ""}
                    {selectedStage.evidence_required ? " · 📎 Evidence" : ""}
                  </Text>
                  {canBulkSelect ? (
                    <TouchableOpacity onPress={() => { setBulkMode((v) => !v); setSelectedTicketIds([]); }}>
                      <Text style={styles.bulkToggle}>{bulkMode ? "Cancel" : "Select"}</Text>
                    </TouchableOpacity>
                  ) : null}
                </View>
              ) : null}

              {stageTickets.map((t) => (
                <TicketCard
                  key={t.id}
                  t={t}
                  bulkMode={bulkMode}
                  selected={selectedTicketIds.includes(t.id)}
                  onPress={() => (bulkMode ? toggleBulkSelect(t.id) : openDetail(t.id))}
                />
              ))}
              {stageTickets.length === 0 ? <Text style={styles.emptyText}>No tickets at this stage.</Text> : null}
            </>
          ) : (
            <>
              <View style={styles.statusFilterRow}>
                {[{ key: "" as const, label: "All" }, { key: "ACTIVE" as const, label: "Active" }, { key: "OVERDUE" as const, label: "Overdue" }, { key: "CLOSED" as const, label: "Closed" }].map((f) => {
                  const active = statusFilter === f.key;
                  return (
                    <TouchableOpacity key={f.key} style={[styles.statusFilterChip, active && styles.statusFilterChipActive]} onPress={() => setStatusFilter(f.key)}>
                      <Text style={[styles.statusFilterText, active && styles.statusFilterTextActive]}>{f.label}</Text>
                    </TouchableOpacity>
                  );
                })}
              </View>
              {tableTickets.map((t) => (
                <TableTicketCard key={t.id} t={t} stages={stages} onPress={() => openDetail(t.id)} />
              ))}
              {tableTickets.length === 0 ? <Text style={styles.emptyText}>No tickets match this filter.</Text> : null}
            </>
          )}
        </ScrollView>
      ) : null}

      {bulkMode && selectedTicketIds.length > 0 && nextStageObj ? (
        <TouchableOpacity style={styles.bulkBar} onPress={submitBulkTransition}>
          <Text style={styles.bulkBarText}>{selectedTicketIds.length} selected</Text>
          <View style={styles.bulkBarButton}>
            <Text style={styles.bulkBarButtonText}>Move to {nextStageObj.name} →</Text>
          </View>
        </TouchableOpacity>
      ) : null}

      {/* Flow picker */}
      <BottomSheet visible={flowPickerOpen} onClose={() => setFlowPickerOpen(false)}>
        <Text style={styles.sheetTitle}>Choose a flow</Text>
        {(flows ?? []).map((f) => (
          <TouchableOpacity key={f.id} style={styles.flowRow} onPress={() => { setActiveFlowId(f.id); setSelectedStageId(f.stages[0]?.id ?? null); setStatusFilter(""); setFlowPickerOpen(false); }}>
            <View style={[styles.flowDot, { backgroundColor: f.color }]} />
            <Text style={styles.flowRowName}>{f.name}</Text>
            <Text style={styles.flowRowSub}>{f.stages.length} stages</Text>
          </TouchableOpacity>
        ))}
      </BottomSheet>

      {/* Create sheet */}
      <BottomSheet visible={createOpen} onClose={() => setCreateOpen(false)}>
        <Text style={styles.sheetTitle}>New Flow Ticket</Text>
        <Text style={styles.sheetLabel}>Workflow</Text>
        <View style={styles.chipWrap}>
          {(flows ?? []).map((f) => {
            const p = chipPair(createFlowId === f.id, f.color);
            return (
              <TouchableOpacity key={f.id} style={p.box as any} onPress={() => { setCreateFlowId(f.id); setCreateStageId(f.stages[0]?.id ?? null); }}>
                <Text style={p.text as any}>{f.name}</Text>
              </TouchableOpacity>
            );
          })}
        </View>
        <Text style={styles.sheetLabel}>Starting stage</Text>
        <View style={styles.chipWrap}>
          {(createFlowObj?.stages ?? []).map((s) => {
            const p = chipPair(createStageId === s.id, s.color);
            return (
              <TouchableOpacity key={s.id} style={p.box as any} onPress={() => setCreateStageId(s.id)}>
                <Text style={p.text as any}>{s.name}</Text>
              </TouchableOpacity>
            );
          })}
        </View>
        <Text style={styles.sheetLabel}>Title</Text>
        <TextInput style={styles.input} placeholder="e.g. Steel Frame Batch 14" placeholderTextColor="#64748b" value={createTitle} onChangeText={setCreateTitle} />
        <Text style={[styles.sheetLabel, { marginTop: 14 }]}>Priority</Text>
        <View style={styles.rowChips}>
          {PRIORITY_OPTIONS.map((p) => {
            const meta = PRIORITY_META[p];
            const cp = chipPair(createPriority === p, meta.fg);
            return (
              <TouchableOpacity key={p} style={cp.box as any} onPress={() => setCreatePriority(p)}>
                <Text style={cp.text as any}>{meta.label}</Text>
              </TouchableOpacity>
            );
          })}
        </View>
        <Text style={styles.sheetLabel}>Due date</Text>
        <TextInput style={styles.input} placeholder="e.g. 28 Jul" placeholderTextColor="#64748b" value={createDue} onChangeText={setCreateDue} />
        <Text style={[styles.sheetLabel, { marginTop: 14 }]}>Assignee</Text>
        <TouchableOpacity style={styles.pickerField} onPress={() => openEmployeePicker("create")}>
          <Text style={{ fontSize: 14, color: createAssigneeId ? "#e2e8f0" : "#64748b" }}>
            {createAssigneeId ? employeeOptions.find((e) => e.id === createAssigneeId)?.name : "Select employee…"}
          </Text>
        </TouchableOpacity>
        <View style={styles.sheetActions}>
          <TouchableOpacity style={styles.clearButton} onPress={() => setCreateOpen(false)}>
            <Text style={styles.clearButtonText}>Cancel</Text>
          </TouchableOpacity>
          <TouchableOpacity style={[styles.applyButton, (!canSubmitCreate || creating) && { opacity: 0.5 }]} onPress={submitCreate} disabled={!canSubmitCreate || creating}>
            {creating ? <ActivityIndicator color="#fff" /> : <Text style={styles.applyButtonText}>Create Ticket</Text>}
          </TouchableOpacity>
        </View>
      </BottomSheet>

      {/* Detail sheet */}
      <BottomSheet visible={!!detailTicketId} onClose={closeDetail}>
        {detail ? (
          <>
            <View style={styles.rowChips}>
              <Text style={styles.detailDisplayId}>{detail.display_id}</Text>
              {detail.is_flagged ? <Text>🚩</Text> : null}
            </View>
            <Text style={styles.detailTitle}>{detail.title}</Text>
            <View style={styles.rowChips}>
              <View style={[styles.pill, { backgroundColor: PRIORITY_META[detail.priority].bg }]}>
                <Text style={[styles.pillText, { color: PRIORITY_META[detail.priority].fg }]}>{PRIORITY_META[detail.priority].label}</Text>
              </View>
              <View style={[styles.pill, { backgroundColor: STATUS_META[detail.status]?.bg ?? GRAY_BG }]}>
                <Text style={[styles.pillText, { color: STATUS_META[detail.status]?.fg ?? GRAY_FG }]}>{STATUS_META[detail.status]?.label ?? detail.status}</Text>
              </View>
              {detail.status === "ON_HOLD" && detail.linked_child_ticket_id ? (
                <View style={[styles.pill, { backgroundColor: "rgba(245,158,11,0.14)" }]}>
                  <Text style={[styles.pillText, { color: "#f59e0b" }]}>⏸ Paused{detail.pause_reason ? `: ${detail.pause_reason}` : ""}</Text>
                </View>
              ) : null}
              {detail.linked_parent_ticket_id ? (
                <View style={[styles.pill, { backgroundColor: "rgba(56,189,248,0.14)" }]}>
                  <Text style={[styles.pillText, { color: "#38bdf8" }]}>🔗 Blocking {detail.linked_parent_display_id}</Text>
                </View>
              ) : null}
              {detail.continued_from_display_id ? (
                <View style={[styles.pill, { backgroundColor: "rgba(52,211,153,0.14)" }]}>
                  <Text style={[styles.pillText, { color: "#34d399" }]}>← Continued from {detail.continued_from_display_id}</Text>
                </View>
              ) : null}
              {detail.continued_to_display_id ? (
                <View style={[styles.pill, { backgroundColor: "rgba(52,211,153,0.14)" }]}>
                  <Text style={[styles.pillText, { color: "#34d399" }]}>→ Continued as {detail.continued_to_display_id}</Text>
                </View>
              ) : null}
            </View>

            <Text style={styles.sectionLabel}>Flow Progress</Text>
            {stages.map((s) => {
              const isCurrent = s.id === detail.current_stage_id;
              const isDone = s.order < (detail.current_stage_order ?? 0);
              const isFuture = s.order > (detail.current_stage_order ?? 0);
              const expanded = expandedStageRow === s.id;
              const hist = detail.stage_history.filter((h) => h.stage_id === s.id).slice(-1)[0];
              return (
                <View key={s.id} style={{ marginBottom: 6 }}>
                  <TouchableOpacity
                    style={[
                      styles.stepperRow,
                      { backgroundColor: isCurrent ? s.color + "1a" : isDone ? "rgba(34,197,94,0.06)" : "#111827", borderColor: isCurrent ? s.color : isDone ? "rgba(34,197,94,0.25)" : "rgba(255,255,255,0.06)" },
                    ]}
                    onPress={() => setExpandedStageRow(expanded ? null : s.id)}
                  >
                    <View style={[styles.stepperDot, { backgroundColor: isDone ? GREEN : isCurrent ? s.color : "rgba(255,255,255,0.15)" }]} />
                    <Text style={[styles.stepperLabel, { color: isFuture ? "#64748b" : "#e2e8f0" }]}>{s.name}</Text>
                    <Text style={styles.stepperBadge}>{isCurrent ? "● Now" : isDone ? "✓ Done" : "Upcoming"}</Text>
                  </TouchableOpacity>
                  {expanded ? (
                    <View style={styles.stepperExpand}>
                      <View style={styles.stepperExpandRow}>
                        <Text style={styles.stepperExpandLabel}>Assignee</Text>
                        <Text style={styles.stepperExpandValue}>{isCurrent ? detail.assignee_name ?? "—" : hist?.assignee_name ?? "—"}</Text>
                      </View>
                      <View style={styles.stepperExpandRow}>
                        <Text style={styles.stepperExpandLabel}>Time in stage</Text>
                        <Text style={styles.stepperExpandValue}>
                          {hist?.entered_at ? new Date(hist.entered_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }) : "—"}
                        </Text>
                      </View>
                      {s.target_tat_hours ? (
                        <View style={styles.stepperExpandRow}>
                          <Text style={styles.stepperExpandLabel}>TaT target</Text>
                          <Text style={styles.stepperExpandValue}>{s.target_tat_hours}h</Text>
                        </View>
                      ) : null}
                      {s.evidence_required && isCurrent ? <Text style={styles.stepperEvidenceNote}>📎 Evidence required to exit this stage</Text> : null}
                    </View>
                  ) : null}
                </View>
              );
            })}

            {canTransitionSelected ? (
              <TouchableOpacity style={styles.moveButton} onPress={openTransition}>
                <Text style={styles.moveButtonText}>↗ Move to Next Stage</Text>
              </TouchableOpacity>
            ) : null}
            <TouchableOpacity style={styles.moreActionsButton} onPress={() => setActionsOpen(true)}>
              <Text style={styles.moreActionsText}>More Actions</Text>
            </TouchableOpacity>

            <Text style={[styles.sectionLabel, { marginTop: 20 }]}>Activity</Text>
            {detailEvents.map((h, idx) => (
              <View key={idx} style={styles.activityRow}>
                <View style={styles.activityDot} />
                <View style={{ flex: 1 }}>
                  <Text style={styles.activityLabel}>{h.event_type.replace(/_/g, " ")}</Text>
                  <Text style={styles.activityMeta}>{h.actor_name} · {h.created_at}</Text>
                  {h.detail ? <Text style={styles.activityDetail}>{h.detail}</Text> : null}
                </View>
              </View>
            ))}
          </>
        ) : (
          <ActivityIndicator color={TEAL} />
        )}
      </BottomSheet>

      {/* Transition sheet */}
      <BottomSheet visible={transitionOpen} onClose={() => setTransitionOpen(false)}>
        <Text style={styles.sheetTitle}>Move to Stage</Text>
        <Text style={styles.sheetLabel}>Next stage</Text>
        <View style={styles.chipWrap}>
          {validStages.map((s) => {
            const active = transNextStageId === s.id;
            const back = curStageDef ? s.order < curStageDef.order : false;
            const p = chipPair(active, s.color);
            return (
              <TouchableOpacity key={s.id} style={p.box as any} onPress={() => setTransNextStageId(s.id)}>
                <Text style={p.text as any}>{s.name}{back ? " (back)" : ""}</Text>
              </TouchableOpacity>
            );
          })}
        </View>

        {showReturnReason ? (
          <>
            <Text style={[styles.sheetLabel, { color: YELLOW }]}>Return reason *</Text>
            <TextInput style={[styles.input, { borderColor: "rgba(234,179,8,0.3)" }]} placeholder="Why is this being returned…" placeholderTextColor="#64748b" value={transReturnReason} onChangeText={setTransReturnReason} />
          </>
        ) : null}

        <Text style={[styles.sheetLabel, { marginTop: 14 }]}>New assignee</Text>
        <TouchableOpacity style={styles.pickerField} onPress={() => openEmployeePicker("transition")}>
          <Text style={{ fontSize: 14, color: transAssigneeId ? "#e2e8f0" : "#64748b" }}>
            {transAssigneeId ? employeeOptions.find((e) => e.id === transAssigneeId)?.name : "Select employee…"}
          </Text>
        </TouchableOpacity>

        <Text style={styles.sheetLabel}>Completion note{noteRequired ? " *" : ""}</Text>
        <TextInput style={styles.input} placeholder="What was accomplished…" placeholderTextColor="#64748b" value={transNote} onChangeText={setTransNote} />

        {requiredCustomFields.map((f) => (
          <View key={f.id}>
            <Text style={[styles.sheetLabel, { color: "#a78bfa" }]}>{f.label}</Text>
            <TextInput
              style={[styles.input, { backgroundColor: "rgba(167,139,250,0.06)", borderColor: "rgba(167,139,250,0.3)" }]}
              placeholder={f.label}
              placeholderTextColor="#64748b"
              value={transCustomValues[f.id] ?? ""}
              onChangeText={(v) => setTransCustomValues((prev) => ({ ...prev, [f.id]: v }))}
            />
          </View>
        ))}

        {showEvidence ? (
          <View style={styles.evidenceBox}>
            <Text style={styles.evidenceBoxTitle}>📎 Evidence required *</Text>
            {transEvidenceUri ? (
              <View style={styles.evidenceAttached}>
                <Text style={styles.evidenceAttachedText}>File attached</Text>
                <TouchableOpacity onPress={() => setTransEvidenceUri(null)}>
                  <Text style={{ color: "#5eead4", fontSize: 13 }}>✕</Text>
                </TouchableOpacity>
              </View>
            ) : (
              <TouchableOpacity style={styles.evidenceEmpty} onPress={attachTransitionEvidence}>
                <Text style={styles.evidenceEmptyText}>Attach photo / file</Text>
              </TouchableOpacity>
            )}
          </View>
        ) : null}

        {canManage ? (
          <TouchableOpacity style={styles.checkboxRow} onPress={() => setTransOverride((v) => !v)}>
            <View style={[styles.checkbox, transOverride && { backgroundColor: YELLOW, borderColor: YELLOW }]} />
            <Text style={styles.overrideLabel}>⚠ Manager override (bypass sequence)</Text>
          </TouchableOpacity>
        ) : null}

        <View style={styles.sheetActions}>
          <TouchableOpacity style={styles.clearButton} onPress={() => setTransitionOpen(false)}>
            <Text style={styles.clearButtonText}>Cancel</Text>
          </TouchableOpacity>
          <TouchableOpacity style={[styles.applyButtonTeal, (!canSubmitTransition || transSubmitting) && { opacity: 0.5 }]} onPress={submitTransition} disabled={!canSubmitTransition || transSubmitting}>
            {transSubmitting ? <ActivityIndicator color="#0b0f1a" /> : <Text style={styles.applyButtonTealText}>Move Ticket</Text>}
          </TouchableOpacity>
        </View>
      </BottomSheet>

      {/* Actions sheet */}
      <BottomSheet visible={actionsOpen} onClose={() => setActionsOpen(false)}>
        <Text style={styles.sheetTitle}>Ticket Actions</Text>
        <View style={{ gap: 8 }}>
          <ActionRow label="💬 Add Comment" onPress={() => openSubAction("comment")} />
          <ActionRow label={detail?.is_flagged ? "🚩 Remove Flag" : "🚩 Flag Ticket"} color={detail?.is_flagged ? "#f87171" : undefined} onPress={() => openSubAction(detail?.is_flagged ? "unflag" : "flag")} />
          {canManage ? (
            <>
              <ActionRow label="↩ Reassign" onPress={() => openSubAction("reassign")} />
              <ActionRow label={detail?.status === "ON_HOLD" ? "▶ Resume" : "⏸ Put On Hold"} onPress={() => openSubAction(detail?.status === "ON_HOLD" ? "resume" : "on_hold")} />
              <ActionRow label="👥 Add Helper" onPress={() => openSubAction("add_helper")} />
            </>
          ) : null}
          <ActionRow label="⚑ Request Help" color="#fcd34d" onPress={() => { setSubText(""); setActionsOpen(false); setHelpSheetOpen(true); }} />
          {canManage && detail?.status === "ON_HOLD" && detail?.linked_child_ticket_id ? (
            <ActionRow label="▶ Resume (linked)" onPress={() => openSubAction("resume")} />
          ) : null}
          {canManage && detail?.status !== "ON_HOLD" && detail?.status !== "COMPLETED" && detail?.status !== "CLOSED" &&
          flows?.find((f) => f.id === detail?.flow_id)?.stages.find((s) => s.id === detail?.current_stage_id)?.has_linked_flow ? (
            <ActionRow label="🔗 Send to Linked Flow" onPress={() => openSubAction("send_to_linked_flow")} />
          ) : null}
          {canManage && detail?.status !== "COMPLETED" && detail?.status !== "CLOSED" &&
          flows?.find((f) => f.id === detail?.flow_id)?.has_next_flow ? (
            <ActionRow label="✔ Close & Continue" onPress={() => openSubAction("close_and_continue")} />
          ) : null}
          <ActionRow label="✕ Close Ticket" danger onPress={() => openSubAction("close")} />
        </View>
      </BottomSheet>

      {/* Sub-action sheet */}
      <BottomSheet visible={!!subAction} onClose={() => setSubAction(null)}>
        {subAction ? (
          <>
            <Text style={styles.sheetTitle}>{SUB_META[subAction].title}</Text>
            {SUB_META[subAction].needsAssignee ? (
              <>
                <Text style={styles.sheetLabel}>{SUB_META[subAction].assigneeLabel}</Text>
                <TouchableOpacity style={styles.pickerField} onPress={() => openEmployeePicker("sub")}>
                  <Text style={{ fontSize: 14, color: subAssigneeId ? "#e2e8f0" : "#64748b" }}>
                    {subAssigneeId ? employeeOptions.find((e) => e.id === subAssigneeId)?.name : "Select employee…"}
                  </Text>
                </TouchableOpacity>
              </>
            ) : null}
            <Text style={styles.sheetLabel}>{SUB_META[subAction].textLabel}</Text>
            <TextInput
              style={[styles.input, styles.inputMultiline]}
              placeholder={SUB_META[subAction].placeholder}
              placeholderTextColor="#64748b"
              value={subText}
              onChangeText={setSubText}
              multiline
            />
            <View style={styles.sheetActions}>
              <TouchableOpacity style={styles.clearButton} onPress={() => setSubAction(null)}>
                <Text style={styles.clearButtonText}>Cancel</Text>
              </TouchableOpacity>
              <TouchableOpacity style={[styles.applyButton, subSubmitting && { opacity: 0.6 }]} onPress={submitSubAction} disabled={subSubmitting}>
                {subSubmitting ? <ActivityIndicator color="#fff" /> : <Text style={styles.applyButtonText}>{SUB_META[subAction].submitLabel}</Text>}
              </TouchableOpacity>
            </View>
          </>
        ) : null}
      </BottomSheet>

      {/* Help request sheet (dedicated endpoint, distinct field from comment sub-actions) */}
      <BottomSheet visible={helpSheetOpen} onClose={() => setHelpSheetOpen(false)}>
        <Text style={styles.sheetTitle}>Request Help</Text>
        <Text style={styles.sheetLabel}>Issue description</Text>
        <TextInput style={[styles.input, styles.inputMultiline]} placeholder="Describe the issue…" placeholderTextColor="#64748b" value={subText} onChangeText={setSubText} multiline />
        <View style={styles.sheetActions}>
          <TouchableOpacity style={styles.clearButton} onPress={() => setHelpSheetOpen(false)}>
            <Text style={styles.clearButtonText}>Cancel</Text>
          </TouchableOpacity>
          <TouchableOpacity style={[styles.applyButton, subSubmitting && { opacity: 0.6 }]} onPress={submitHelpRequest} disabled={subSubmitting}>
            {subSubmitting ? <ActivityIndicator color="#fff" /> : <Text style={styles.applyButtonText}>Send Request</Text>}
          </TouchableOpacity>
        </View>
      </BottomSheet>

      <EmployeePicker
        visible={employeePickerOpen}
        title="Choose employee"
        employees={employeeOptions}
        onSelect={onEmployeePicked}
        onClose={() => setEmployeePickerOpen(false)}
      />
    </View>
  );
}

function ActionRow({ label, onPress, color, danger }: { label: string; onPress: () => void; color?: string; danger?: boolean }) {
  return (
    <TouchableOpacity style={[styles.actionRow, danger && styles.actionRowDanger]} onPress={onPress}>
      <Text style={[styles.actionRowText, color ? { color } : null, danger && { color: "#f87171" }]}>{label}</Text>
    </TouchableOpacity>
  );
}

function LinkBadges({ t }: { t: FMSTicket }) {
  const badges: { label: string; fg: string; bg: string }[] = [];
  if (t.status === "ON_HOLD" && t.linked_child_ticket_id) {
    badges.push({ label: `⏸ Paused${t.linked_child_display_id ? ` — ${t.linked_child_display_id}` : ""}`, fg: "#f59e0b", bg: "rgba(245,158,11,0.14)" });
  }
  if (t.linked_parent_ticket_id) {
    badges.push({ label: `🔗 Blocking ${t.linked_parent_display_id ?? ""}`, fg: "#38bdf8", bg: "rgba(56,189,248,0.14)" });
  }
  if (t.continued_from_display_id) {
    badges.push({ label: `← From ${t.continued_from_display_id}`, fg: "#34d399", bg: "rgba(52,211,153,0.14)" });
  }
  if (t.continued_to_display_id) {
    badges.push({ label: `→ Continued ${t.continued_to_display_id}`, fg: "#34d399", bg: "rgba(52,211,153,0.14)" });
  }
  if (!badges.length) return null;
  return (
    <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 4, marginTop: 4 }}>
      {badges.map((b, i) => (
        <View key={i} style={[styles.pill, { backgroundColor: b.bg }]}>
          <Text style={[styles.pillText, { color: b.fg, fontSize: 10 }]}>{b.label}</Text>
        </View>
      ))}
    </View>
  );
}

function TicketCard({ t, bulkMode, selected, onPress }: { t: FMSTicket; bulkMode: boolean; selected: boolean; onPress: () => void }) {
  const pm = PRIORITY_META[t.priority];
  const dueColor = t.tat_pct != null && t.tat_pct >= 100 ? RED : "#94a3b8";
  return (
    <TouchableOpacity style={styles.ticketCard} onPress={onPress}>
      <View style={styles.ticketCardHeader}>
        {bulkMode ? <View style={[styles.checkbox, selected && { backgroundColor: INDIGO, borderColor: INDIGO }]} /> : null}
        <View style={{ flex: 1, minWidth: 0 }}>
          <View style={styles.rowChips}>
            <Text style={styles.ticketDisplayId}>{t.display_id}</Text>
            {t.is_flagged ? <Text style={{ fontSize: 12 }}>🚩</Text> : null}
          </View>
          <Text style={styles.ticketTitle}>{t.title}</Text>
          <Text style={styles.ticketMeta}>{t.assignee_name ?? "Unassigned"} · <Text style={{ color: dueColor }}>{fmtDate(t.due_at)}</Text></Text>
        </View>
        <View style={[styles.pill, { backgroundColor: pm.bg }]}>
          <Text style={[styles.pillText, { color: pm.fg }]}>{pm.label}</Text>
        </View>
      </View>
      <LinkBadges t={t} />
      {t.tat_pct != null ? (
        <View style={styles.tatTrack}>
          <View style={[styles.tatFill, { width: `${Math.min(t.tat_pct, 100)}%`, backgroundColor: tatColor(t.tat_pct) }]} />
        </View>
      ) : null}
    </TouchableOpacity>
  );
}

function TableTicketCard({ t, stages, onPress }: { t: FMSTicket; stages: FMSStage[]; onPress: () => void }) {
  const pm = PRIORITY_META[t.priority];
  const sm = STATUS_META[t.status] ?? STATUS_META.ACTIVE;
  const dueColor = t.tat_pct != null && t.tat_pct >= 100 ? RED : "#94a3b8";
  return (
    <TouchableOpacity style={styles.ticketCard} onPress={onPress}>
      <View style={styles.ticketCardHeader}>
        <View style={{ flex: 1, minWidth: 0 }}>
          <View style={styles.rowChips}>
            <Text style={styles.ticketDisplayId}>{t.display_id}</Text>
            {t.is_flagged ? <Text style={{ fontSize: 12 }}>🚩</Text> : null}
          </View>
          <Text style={styles.ticketTitle}>{t.title}</Text>
          <Text style={styles.ticketMeta}>{t.assignee_name ?? "Unassigned"} · <Text style={{ color: dueColor }}>{fmtDate(t.due_at)}</Text></Text>
        </View>
        <View style={{ alignItems: "flex-end", gap: 5 }}>
          <View style={[styles.pill, { backgroundColor: pm.bg }]}>
            <Text style={[styles.pillText, { color: pm.fg }]}>{pm.label}</Text>
          </View>
          <View style={[styles.pill, { backgroundColor: sm.bg }]}>
            <Text style={[styles.pillText, { color: sm.fg, fontSize: 10 }]}>{sm.label}</Text>
          </View>
        </View>
      </View>
      <View style={styles.stepDotsRow}>
        {stages.map((s) => {
          const st = s.order < (t.current_stage_order ?? -1) ? "done" : s.order === t.current_stage_order ? "current" : "future";
          const bg = st === "done" ? GREEN : st === "current" ? s.color : "rgba(255,255,255,0.12)";
          const size = st === "current" ? 9 : 7;
          return <View key={s.id} style={{ width: size, height: size, borderRadius: size / 2, backgroundColor: bg }} />;
        })}
        <Text style={styles.stepDotsLabel}>{t.current_stage_name}</Text>
      </View>
      <LinkBadges t={t} />
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: "#0b0f1a" },
  topBar: { paddingTop: 54, paddingHorizontal: 20, paddingBottom: 10, flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  topBarLeft: { flexDirection: "row", alignItems: "center", gap: 12 },
  iconButton: { width: 34, height: 34, borderRadius: 10, backgroundColor: "#111827", borderWidth: 1, borderColor: "rgba(255,255,255,0.08)", alignItems: "center", justifyContent: "center" },
  backIcon: { fontSize: 16, color: "#cbd5e1" },
  title: { fontSize: 17.5, fontWeight: "800", color: "#f1f5f9" },
  addButton: { width: 34, height: 34, borderRadius: 10, backgroundColor: INDIGO, alignItems: "center", justifyContent: "center" },
  addButtonText: { fontSize: 19, fontWeight: "700", color: "#fff", marginTop: -2 },
  flowBar: { marginHorizontal: 20, marginBottom: 12, height: 46, borderRadius: 12, backgroundColor: "#111827", borderWidth: 1, borderColor: "rgba(255,255,255,0.08)", flexDirection: "row", alignItems: "center", paddingHorizontal: 14, gap: 9 },
  flowDot: { width: 9, height: 9, borderRadius: 5 },
  flowBarName: { fontSize: 13.5, fontWeight: "700", color: "#f1f5f9", flex: 1 },
  flowBarSub: { fontSize: 11, color: "#64748b" },
  error: { color: "#f87185", fontSize: 13, marginHorizontal: 20, marginBottom: 8 },
  body: { flex: 1 },
  bodyContent: { paddingHorizontal: 20, paddingBottom: 100 },
  kpiGrid: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginBottom: 8 },
  kpiCard: { width: "48%", backgroundColor: "#111827", borderWidth: 1, borderColor: "rgba(255,255,255,0.08)", borderRadius: 12, padding: 12 },
  kpiCardBreach: { borderColor: "rgba(239,68,68,0.3)" },
  kpiCardFlag: { borderColor: "rgba(245,158,11,0.3)" },
  kpiValue: { fontSize: 20, fontWeight: "800", color: "#e2e8f0" },
  kpiLabel: { fontSize: 10.5, color: "#64748b", marginTop: 2 },
  complianceRow: { backgroundColor: "#111827", borderWidth: 1, borderColor: "rgba(16,185,129,0.25)", borderRadius: 12, padding: 12, marginBottom: 14, flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  complianceLabel: { fontSize: 11.5, color: "#94a3b8", fontWeight: "600" },
  compliancePct: { fontSize: 18, fontWeight: "800", color: GREEN },
  toggleRow: { flexDirection: "row", gap: 8, marginBottom: 12 },
  togglePair: { flex: 1, flexDirection: "row", height: 36, borderRadius: 10, backgroundColor: "#111827", borderWidth: 1, borderColor: "rgba(255,255,255,0.08)", overflow: "hidden" },
  toggleHalf: { flex: 1, alignItems: "center", justifyContent: "center" },
  toggleHalfActive: { backgroundColor: "rgba(45,212,191,0.14)" },
  toggleText: { fontSize: 12, fontWeight: "700", color: "#94a3b8" },
  toggleTextActive: { color: TEAL },
  stageChipRow: { flexDirection: "row", gap: 6, flexWrap: "wrap", marginBottom: 12 },
  stageChip: { flexDirection: "row", alignItems: "center", gap: 6, paddingHorizontal: 12, paddingVertical: 8, borderRadius: 999, borderWidth: 1 },
  stageChipDot: { width: 7, height: 7, borderRadius: 4 },
  stageChipText: { fontSize: 12, fontWeight: "700" },
  stageChipCount: { fontSize: 10, fontWeight: "800", backgroundColor: "rgba(255,255,255,0.06)", borderRadius: 8, paddingHorizontal: 5, paddingVertical: 1 },
  stageSummaryRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: 10 },
  stageSummaryText: { fontSize: 11.5, color: "#94a3b8", flex: 1 },
  bulkToggle: { fontSize: 12, fontWeight: "700", color: "#94a3b8" },
  emptyText: { textAlign: "center", color: "#64748b", fontSize: 13, paddingVertical: 30 },
  statusFilterRow: { flexDirection: "row", gap: 6, marginBottom: 12 },
  statusFilterChip: { flex: 1, height: 36, borderRadius: 10, backgroundColor: "#111827", borderWidth: 1, borderColor: "rgba(255,255,255,0.08)", alignItems: "center", justifyContent: "center" },
  statusFilterChipActive: { backgroundColor: "rgba(45,212,191,0.14)", borderColor: "rgba(45,212,191,0.35)" },
  statusFilterText: { fontSize: 12.5, fontWeight: "600", color: "#94a3b8" },
  statusFilterTextActive: { color: TEAL },
  ticketCard: { backgroundColor: "#111827", borderWidth: 1, borderColor: "rgba(255,255,255,0.08)", borderRadius: 14, padding: 14, marginBottom: 10 },
  ticketCardHeader: { flexDirection: "row", alignItems: "flex-start", gap: 10 },
  ticketDisplayId: { fontSize: 12, fontWeight: "700", color: "#60a5fa" },
  ticketTitle: { fontSize: 13.5, fontWeight: "700", color: "#f1f5f9", marginTop: 3 },
  ticketMeta: { fontSize: 11.5, color: "#94a3b8", marginTop: 3 },
  pill: { paddingVertical: 3, paddingHorizontal: 8, borderRadius: 6 },
  pillText: { fontSize: 10.5, fontWeight: "700" },
  tatTrack: { height: 4, borderRadius: 2, backgroundColor: "rgba(255,255,255,0.08)", overflow: "hidden", marginTop: 10 },
  tatFill: { height: "100%", borderRadius: 2 },
  stepDotsRow: { flexDirection: "row", alignItems: "center", gap: 4, marginTop: 11, flexWrap: "wrap" },
  stepDotsLabel: { fontSize: 10.5, color: "#64748b", marginLeft: 6 },
  bulkBar: { position: "absolute", left: 20, right: 20, bottom: 20, backgroundColor: "#111827", borderWidth: 1, borderColor: "rgba(102,87,242,0.4)", borderRadius: 14, padding: 12, flexDirection: "row", alignItems: "center", gap: 10 },
  bulkBarText: { fontSize: 12, color: "#94a3b8", flex: 1 },
  bulkBarButton: { height: 38, paddingHorizontal: 14, borderRadius: 10, backgroundColor: INDIGO, alignItems: "center", justifyContent: "center" },
  bulkBarButtonText: { fontSize: 12.5, fontWeight: "700", color: "#fff" },
  sheetTitle: { fontSize: 16, fontWeight: "800", color: "#f1f5f9", marginBottom: 16 },
  sheetLabel: { fontSize: 12, fontWeight: "600", color: "#94a3b8", marginBottom: 7, marginTop: 4 },
  flowRow: { height: 52, borderRadius: 12, backgroundColor: "#0d1424", borderWidth: 1, borderColor: "rgba(255,255,255,0.1)", flexDirection: "row", alignItems: "center", gap: 10, paddingHorizontal: 14, marginBottom: 8 },
  flowRowName: { fontSize: 13.5, fontWeight: "700", color: "#e2e8f0", flex: 1 },
  flowRowSub: { fontSize: 11, color: "#64748b" },
  chipWrap: { flexDirection: "row", gap: 7, flexWrap: "wrap", marginBottom: 14 },
  rowChips: { flexDirection: "row", gap: 6, alignItems: "center" },
  chip: { paddingVertical: 8, paddingHorizontal: 13, borderRadius: 999, borderWidth: 1 },
  chipInactive: { backgroundColor: "#0d1424", borderColor: "rgba(255,255,255,0.1)" },
  chipText: { fontSize: 12.5, fontWeight: "600" },
  input: { width: "100%", height: 46, borderRadius: 12, backgroundColor: "#0d1424", borderWidth: 1, borderColor: "rgba(255,255,255,0.1)", color: "#e2e8f0", fontSize: 14, paddingHorizontal: 14, marginBottom: 14 },
  inputMultiline: { height: 70, paddingTop: 12, textAlignVertical: "top" },
  pickerField: { width: "100%", height: 46, borderRadius: 12, backgroundColor: "#0d1424", borderWidth: 1, borderColor: "rgba(255,255,255,0.1)", justifyContent: "center", paddingHorizontal: 14, marginBottom: 16 },
  sheetActions: { flexDirection: "row", gap: 10, marginTop: 8 },
  clearButton: { flex: 1, height: 50, borderRadius: 12, borderWidth: 1, borderColor: "rgba(255,255,255,0.12)", alignItems: "center", justifyContent: "center" },
  clearButtonText: { fontSize: 14, fontWeight: "700", color: "#cbd5e1" },
  applyButton: { flex: 2, height: 50, borderRadius: 12, backgroundColor: INDIGO, alignItems: "center", justifyContent: "center" },
  applyButtonText: { fontSize: 15, fontWeight: "700", color: "#fff" },
  applyButtonTeal: { flex: 2, height: 50, borderRadius: 12, backgroundColor: TEAL, alignItems: "center", justifyContent: "center" },
  applyButtonTealText: { fontSize: 15, fontWeight: "700", color: "#0b0f1a" },
  detailDisplayId: { fontSize: 12, fontWeight: "700", color: "#60a5fa" },
  detailTitle: { fontSize: 17, fontWeight: "800", color: "#f1f5f9", marginVertical: 8 },
  sectionLabel: { fontSize: 11, fontWeight: "700", color: "#64748b", textTransform: "uppercase", letterSpacing: 0.5, marginBottom: 8, marginTop: 6 },
  stepperRow: { flexDirection: "row", alignItems: "center", gap: 10, padding: 10, borderRadius: 10, borderWidth: 1 },
  stepperDot: { width: 10, height: 10, borderRadius: 5 },
  stepperLabel: { fontSize: 12.5, fontWeight: "700", flex: 1 },
  stepperBadge: { fontSize: 10.5, color: "#64748b" },
  stepperExpand: { padding: 10, paddingHorizontal: 14, backgroundColor: "#0d1424", borderRadius: 10, marginTop: -2 },
  stepperExpandRow: { flexDirection: "row", justifyContent: "space-between", paddingVertical: 5 },
  stepperExpandLabel: { fontSize: 11.5, color: "#64748b" },
  stepperExpandValue: { fontSize: 11.5, color: "#e2e8f0", fontWeight: "600" },
  stepperEvidenceNote: { fontSize: 11, color: YELLOW, marginTop: 4 },
  moveButton: { marginTop: 16, height: 50, borderRadius: 12, backgroundColor: TEAL, alignItems: "center", justifyContent: "center" },
  moveButtonText: { fontSize: 15, fontWeight: "700", color: "#0b0f1a" },
  moreActionsButton: { marginTop: 10, height: 46, borderRadius: 12, borderWidth: 1, borderColor: "rgba(255,255,255,0.1)", alignItems: "center", justifyContent: "center" },
  moreActionsText: { fontSize: 13.5, fontWeight: "700", color: "#94a3b8" },
  activityRow: { flexDirection: "row", gap: 10, paddingBottom: 12 },
  activityDot: { width: 8, height: 8, borderRadius: 4, backgroundColor: BLUE, marginTop: 5 },
  activityLabel: { fontSize: 11.5, fontWeight: "700", color: "#cbd5e1", textTransform: "capitalize" },
  activityMeta: { fontSize: 10.5, color: "#64748b", marginTop: 1 },
  activityDetail: { fontSize: 11, color: "#94a3b8", marginTop: 3, backgroundColor: "#0d1424", borderRadius: 6, padding: 8 },
  evidenceBox: { backgroundColor: "rgba(234,179,8,0.06)", borderWidth: 1, borderColor: "rgba(234,179,8,0.25)", borderRadius: 12, padding: 14, marginBottom: 14 },
  evidenceBoxTitle: { fontSize: 12, fontWeight: "700", color: YELLOW, marginBottom: 8 },
  evidenceAttached: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", height: 42, borderRadius: 10, backgroundColor: "rgba(45,212,191,0.1)", borderWidth: 1, borderColor: "rgba(45,212,191,0.3)", paddingHorizontal: 12 },
  evidenceAttachedText: { fontSize: 13, fontWeight: "600", color: TEAL },
  evidenceEmpty: { height: 42, borderRadius: 10, borderWidth: 1, borderColor: "rgba(234,179,8,0.4)", borderStyle: "dashed", alignItems: "center", justifyContent: "center" },
  evidenceEmptyText: { fontSize: 13, fontWeight: "600", color: YELLOW },
  checkboxRow: { flexDirection: "row", alignItems: "center", gap: 9, marginBottom: 16 },
  checkbox: { width: 20, height: 20, borderRadius: 6, borderWidth: 1.5, borderColor: "rgba(255,255,255,0.2)" },
  overrideLabel: { fontSize: 12.5, color: "#fcd34d" },
  actionRow: { height: 46, borderRadius: 10, backgroundColor: "#0d1424", borderWidth: 1, borderColor: "rgba(255,255,255,0.08)", justifyContent: "center", paddingHorizontal: 14 },
  actionRowDanger: { backgroundColor: "rgba(239,68,68,0.08)", borderColor: "rgba(239,68,68,0.25)" },
  actionRowText: { fontSize: 13.5, fontWeight: "600", color: "#e2e8f0" },
});
