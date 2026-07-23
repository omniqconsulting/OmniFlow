import { useCallback, useEffect, useMemo, useState } from "react";
import { ActivityIndicator, Alert, ScrollView, StyleSheet, Text, TextInput, TouchableOpacity, View } from "react-native";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import * as ImagePicker from "expo-image-picker";

import type { AuthStackParamList } from "../navigation/AuthNavigator";
import { ApiError } from "../api/client";
import {
  completeChecklist,
  failChecklist,
  listChecklists,
  uploadChecklistEvidence,
  type ChecklistItem,
} from "../api/checklists";
import { listTickets, updateTicket, type Ticket } from "../api/tickets";
import { getBoard, getFlows, getTicket, getTicketEvents, type FMSFlow, type FMSTicket, type FMSTicketDetail, type FMSTicketEvent } from "../api/fms";
import { formatIstDateTime } from "../utils/dateFormat";
import BottomSheet from "../components/BottomSheet";

const TEAL = "#2DD4BF";
const INDIGO = "#6657F2";
const RED = "#ef4444";
const AMBER = "#f59e0b";
const GREEN = "#22c55e";
const BLUE = "#3b82f6";
const YELLOW = "#eab308";
const GRAY_FG = "#94a3b8";
const GRAY_BG = "rgba(148,163,184,0.14)";

type Props = NativeStackScreenProps<AuthStackParamList, "MyTasks">;

const PRIORITY_META: Record<string, { fg: string; bg: string; label: string }> = {
  LOW: { fg: GRAY_FG, bg: GRAY_BG, label: "Low" },
  MEDIUM: { fg: BLUE, bg: "rgba(59,130,246,0.14)", label: "Medium" },
  HIGH: { fg: AMBER, bg: "rgba(245,158,11,0.14)", label: "High" },
  CRITICAL: { fg: RED, bg: "rgba(239,68,68,0.14)", label: "Critical" },
};
const TICKET_STATUS_META: Record<string, { fg: string; bg: string; label: string }> = {
  ACTIVE: { fg: BLUE, bg: "rgba(59,130,246,0.14)", label: "Active" },
  STAGE_COMPLETE: { fg: TEAL, bg: "rgba(45,212,191,0.14)", label: "Stage Complete" },
  IN_TRANSITION: { fg: "#8b5cf6", bg: "rgba(139,92,246,0.14)", label: "In Transition" },
  ON_HOLD: { fg: GRAY_FG, bg: GRAY_BG, label: "On Hold" },
  HELP_REQUESTED: { fg: "#f97316", bg: "rgba(249,115,22,0.14)", label: "Help Needed" },
  FLAGGED: { fg: RED, bg: "rgba(239,68,68,0.14)", label: "Flagged" },
};
const CHECKLIST_STATUS_META: Record<string, { fg: string; bg: string; label: string }> = {
  PENDING: { fg: GRAY_FG, bg: GRAY_BG, label: "Pending" },
  IN_PROGRESS: { fg: BLUE, bg: "rgba(59,130,246,0.14)", label: "In Progress" },
  OVERDUE: { fg: AMBER, bg: "rgba(245,158,11,0.14)", label: "Overdue" },
};

type GenericKind = "CHECKLIST" | "DELEGATION";
const SECTION_META: Record<GenericKind, { icon: string; label: string }> = {
  CHECKLIST: { icon: "✅", label: "Checklists" },
  DELEGATION: { icon: "📨", label: "Delegated Tasks" },
};

type GenericTask = {
  key: string;
  kind: GenericKind;
  title: string;
  description: string;
  meta: string;
  tagLabel: string;
  tagFg: string;
  tagBg: string;
  statusFg: string;
  statusBg: string;
  statusLabel: string;
  due: string;
  dueColor: string;
  evidenceRequired: boolean;
  overdue: boolean;
  checklist?: ChecklistItem;
  ticket?: Ticket;
};

function tatColor(pct: number | null): string {
  if (pct == null) return "#64748b";
  return pct < 50 ? GREEN : pct < 90 ? AMBER : RED;
}

export default function MyTasksScreen({ navigation, route }: Props) {
  const { user } = route.params;

  const [flows, setFlows] = useState<FMSFlow[]>([]);
  const [fmsTickets, setFmsTickets] = useState<FMSTicket[]>([]);
  const [checklists, setChecklists] = useState<ChecklistItem[]>([]);
  const [delegations, setDelegations] = useState<Ticket[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [selectedFmsTicketId, setSelectedFmsTicketId] = useState<string | null>(null);
  const [fmsDetail, setFmsDetail] = useState<FMSTicketDetail | null>(null);
  const [fmsEvents, setFmsEvents] = useState<FMSTicketEvent[]>([]);

  const [selectedTask, setSelectedTask] = useState<GenericTask | null>(null);
  const [completingTask, setCompletingTask] = useState<GenericTask | null>(null);
  const [completeNote, setCompleteNote] = useState("");
  const [evidenceUri, setEvidenceUri] = useState<string | null>(null);
  const [uploadingEvidence, setUploadingEvidence] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [fl, board, cl, tk] = await Promise.all([
        getFlows(),
        getBoard({ myWork: true }),
        listChecklists(),
        listTickets({ status: "OPEN", assigneeId: [user.id], limit: 100 }),
      ]);
      setFlows(fl);
      setFmsTickets(board.tickets.filter((t) => t.status !== "COMPLETED" && t.status !== "CLOSED"));
      setChecklists(cl.filter((c) => c.employee_id === user.id && c.status !== "DONE" && c.status !== "FAILED"));
      setDelegations(tk.items.filter((t) => t.ticket_type === "D"));
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Couldn't load your tasks.");
    } finally {
      setLoading(false);
    }
  }, [user.id]);

  useEffect(() => {
    load();
  }, [load]);

  const ticketCards = useMemo(
    () =>
      fmsTickets.map((t) => {
        const pm = PRIORITY_META[t.priority];
        const sm = TICKET_STATUS_META[t.status] ?? { fg: GRAY_FG, bg: GRAY_BG, label: t.status };
        const overdue = t.tat_pct != null && t.tat_pct >= 100;
        return {
          t,
          priorityFg: pm.fg, priorityBg: pm.bg, priorityLabel: pm.label,
          statusFg: sm.fg, statusBg: sm.bg, statusLabel: sm.label,
          due: formatIstDateTime(t.due_at), dueColor: overdue ? RED : "#94a3b8",
          hasTat: t.tat_pct != null, tatColor: tatColor(t.tat_pct), tatPct: Math.min(t.tat_pct ?? 0, 100),
          overdue,
        };
      }),
    [fmsTickets]
  );

  const genericTasks: GenericTask[] = useMemo(() => {
    const out: GenericTask[] = [];
    for (const c of checklists) {
      const sm = CHECKLIST_STATUS_META[c.status ?? "PENDING"] ?? CHECKLIST_STATUS_META.PENDING;
      const overdue = c.status === "OVERDUE";
      out.push({
        key: `c:${c.template_id}:${c.employee_id}`, kind: "CHECKLIST",
        title: c.title, description: c.description, meta: c.frequency_label,
        tagLabel: c.frequency_label, tagFg: INDIGO, tagBg: "rgba(102,87,242,0.14)",
        statusFg: sm.fg, statusBg: sm.bg, statusLabel: sm.label,
        due: formatIstDateTime(c.due_at), dueColor: overdue ? AMBER : "#64748b",
        evidenceRequired: c.evidence_required, overdue, checklist: c,
      });
    }
    for (const t of delegations) {
      const overdue = !!t.due_at && new Date(t.due_at).getTime() < Date.now();
      out.push({
        key: `d:${t.id}`, kind: "DELEGATION",
        title: t.title, description: t.description, meta: `Delegated by ${t.created_by_name ?? "—"}`,
        tagLabel: "Delegated", tagFg: BLUE, tagBg: "rgba(59,130,246,0.14)",
        statusFg: GRAY_FG, statusBg: GRAY_BG, statusLabel: t.status,
        due: formatIstDateTime(t.due_at), dueColor: overdue ? AMBER : "#64748b",
        evidenceRequired: t.evidence_required, overdue, ticket: t,
      });
    }
    return out;
  }, [checklists, delegations]);

  const sections = useMemo(() => {
    const kindOrder: GenericKind[] = ["CHECKLIST", "DELEGATION"];
    return kindOrder
      .map((k) => ({ kind: k, icon: SECTION_META[k].icon, label: SECTION_META[k].label, items: genericTasks.filter((i) => i.kind === k) }))
      .filter((s) => s.items.length > 0);
  }, [genericTasks]);

  const summary = useMemo(() => {
    const open = ticketCards.length + genericTasks.length;
    const overdue = ticketCards.filter((t) => t.overdue).length + genericTasks.filter((t) => t.overdue).length;
    return { open, overdue };
  }, [ticketCards, genericTasks]);

  const isAllClear = !loading && ticketCards.length === 0 && genericTasks.length === 0;

  // ── FMS ticket detail ────────────────────────────────────────────────
  const openFmsDetail = async (ticketId: string) => {
    setSelectedFmsTicketId(ticketId);
    try {
      const [d, ev] = await Promise.all([getTicket(ticketId), getTicketEvents(ticketId)]);
      setFmsDetail(d);
      setFmsEvents(ev);
    } catch (e) {
      Alert.alert("Couldn't load ticket", e instanceof ApiError ? e.detail : "Something went wrong.");
      setSelectedFmsTicketId(null);
    }
  };
  const closeFmsDetail = () => {
    setSelectedFmsTicketId(null);
    setFmsDetail(null);
    setFmsEvents([]);
  };
  const fmsFlow = flows.find((f) => f.id === fmsDetail?.flow_id) ?? null;
  const nextFmsStage = fmsFlow && fmsDetail ? fmsFlow.stages.find((s) => s.order === (fmsDetail.current_stage_order ?? 0) + 1) ?? null : null;

  const advanceFmsTicket = () => {
    if (!fmsDetail || !fmsFlow) return;
    const ticketId = fmsDetail.id;
    const flowId = fmsFlow.id;
    closeFmsDetail();
    // Evidence, completion notes and custom fields for the target stage are
    // handled by the full Flow Board transition sheet — jump there instead
    // of duplicating that validation here.
    navigation.navigate("FMSFlowBoard", { user, initialFlowId: flowId, initialTicketId: ticketId });
  };

  // ── Generic task detail / complete ──────────────────────────────────
  const openComplete = (task: GenericTask) => {
    setCompletingTask(task);
    setCompleteNote("");
    setEvidenceUri(null);
    setSelectedTask(null);
  };
  const closeComplete = () => setCompletingTask(null);

  const attachEvidence = async () => {
    const perm = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!perm.granted) return;
    const result = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ImagePicker.MediaTypeOptions.Images, quality: 0.7 });
    if (result.canceled || !result.assets[0]) return;
    if (completingTask?.kind === "CHECKLIST" && completingTask.checklist?.assignment_id) {
      setUploadingEvidence(true);
      try {
        await uploadChecklistEvidence(completingTask.checklist.assignment_id, result.assets[0].uri);
        setEvidenceUri(result.assets[0].uri);
      } catch (e) {
        Alert.alert("Couldn't attach evidence", e instanceof ApiError ? e.detail : "Something went wrong.");
      } finally {
        setUploadingEvidence(false);
      }
    }
  };

  const submitComplete = async () => {
    if (!completingTask) return;
    if (completingTask.kind === "CHECKLIST" && completingTask.checklist?.assignment_id) {
      const overdue = completingTask.checklist.status === "OVERDUE";
      if (overdue && !completeNote.trim()) {
        Alert.alert("Delay reason is required for overdue checklists");
        return;
      }
      if (completingTask.checklist.evidence_required && !evidenceUri) {
        Alert.alert("Evidence is required — attach a photo or file before completing");
        return;
      }
      setSubmitting(true);
      try {
        await completeChecklist(completingTask.checklist.assignment_id, completeNote.trim());
        setChecklists((prev) => prev.filter((c) => c.assignment_id !== completingTask.checklist!.assignment_id));
        setCompletingTask(null);
      } catch (e) {
        Alert.alert("Couldn't complete", e instanceof ApiError ? e.detail : "Something went wrong.");
      } finally {
        setSubmitting(false);
      }
      return;
    }
    if (completingTask.kind === "DELEGATION" && completingTask.ticket) {
      setSubmitting(true);
      try {
        await updateTicket(completingTask.ticket.id, { status: "DONE" });
        setDelegations((prev) => prev.filter((t) => t.id !== completingTask.ticket!.id));
        setCompletingTask(null);
      } catch (e) {
        Alert.alert("Couldn't complete", e instanceof ApiError ? e.detail : "Something went wrong.");
      } finally {
        setSubmitting(false);
      }
    }
  };

  const markFailed = async () => {
    if (!completingTask || completingTask.kind !== "CHECKLIST" || !completingTask.checklist?.assignment_id) return;
    setSubmitting(true);
    try {
      await failChecklist(completingTask.checklist.assignment_id, completeNote.trim());
      setChecklists((prev) => prev.filter((c) => c.assignment_id !== completingTask.checklist!.assignment_id));
      setCompletingTask(null);
    } catch (e) {
      Alert.alert("Couldn't mark as failed", e instanceof ApiError ? e.detail : "Something went wrong.");
    } finally {
      setSubmitting(false);
    }
  };

  const canComplete = (t: GenericTask) => (t.kind === "CHECKLIST" ? t.checklist?.status !== "DONE" && t.checklist?.status !== "FAILED" : true);

  return (
    <View style={styles.screen}>
      <View style={styles.topBar}>
        <TouchableOpacity style={styles.iconButton} onPress={() => navigation.goBack()}>
          <Text style={styles.backIcon}>‹</Text>
        </TouchableOpacity>
        <View>
          <Text style={styles.title}>My Tasks</Text>
          <Text style={styles.subtitle}>{user.name}</Text>
        </View>
      </View>

      {error ? <Text style={styles.error}>{error}</Text> : null}
      {loading && !fmsTickets.length && !genericTasks.length ? <ActivityIndicator color={TEAL} style={{ marginTop: 24 }} /> : null}

      <ScrollView style={styles.body} contentContainerStyle={styles.bodyContent}>
        <View style={styles.summaryGrid}>
          <View style={styles.summaryCard}>
            <Text style={styles.summaryValue}>{summary.open}</Text>
            <Text style={styles.summaryLabel}>Open Tasks</Text>
          </View>
          <View style={[styles.summaryCard, { borderColor: summary.overdue > 0 ? "rgba(239,68,68,0.3)" : "rgba(255,255,255,0.08)" }]}>
            <Text style={[styles.summaryValue, { color: summary.overdue > 0 ? RED : "#e2e8f0" }]}>{summary.overdue}</Text>
            <Text style={styles.summaryLabel}>Overdue</Text>
          </View>
        </View>

        {ticketCards.length > 0 ? (
          <>
            <View style={styles.sectionHeader}>
              <Text style={{ fontSize: 13 }}>🔀</Text>
              <Text style={styles.sectionLabel}>Flow Tickets</Text>
              <View style={styles.sectionCount}><Text style={styles.sectionCountText}>{ticketCards.length}</Text></View>
            </View>
            {ticketCards.map((tc) => (
              <TouchableOpacity key={tc.t.id} style={styles.ticketCard} onPress={() => openFmsDetail(tc.t.id)}>
                <View style={styles.ticketRow}>
                  <View style={{ flex: 1, minWidth: 0 }}>
                    <View style={styles.rowChips}>
                      <Text style={styles.ticketDisplayId}>{tc.t.display_id}</Text>
                      {tc.t.is_flagged ? <Text style={{ fontSize: 12 }}>🚩</Text> : null}
                    </View>
                    <Text style={styles.ticketTitle}>{tc.t.title}</Text>
                    <Text style={styles.ticketMeta}>
                      {tc.t.current_stage_name ?? "—"} · <Text style={{ color: tc.dueColor }}>{tc.due}</Text>
                    </Text>
                  </View>
                  <View style={[styles.pill, { backgroundColor: tc.priorityBg }]}>
                    <Text style={[styles.pillText, { color: tc.priorityFg }]}>{tc.priorityLabel}</Text>
                  </View>
                </View>
                {tc.hasTat ? (
                  <View style={styles.tatTrack}>
                    <View style={[styles.tatBar, { width: `${tc.tatPct}%`, backgroundColor: tc.tatColor }]} />
                  </View>
                ) : null}
              </TouchableOpacity>
            ))}
          </>
        ) : null}

        {sections.map((sec) => (
          <View key={sec.kind}>
            <View style={[styles.sectionHeader, { marginTop: 18 }]}>
              <Text style={{ fontSize: 13 }}>{sec.icon}</Text>
              <Text style={styles.sectionLabel}>{sec.label}</Text>
              <View style={styles.sectionCount}><Text style={styles.sectionCountText}>{sec.items.length}</Text></View>
            </View>
            {sec.items.map((item) => (
              <TouchableOpacity key={item.key} style={styles.taskCard} onPress={() => setSelectedTask(item)}>
                <View style={styles.taskRow}>
                  <View style={[styles.taskDot, { backgroundColor: item.statusFg }]} />
                  <View style={{ flex: 1, minWidth: 0 }}>
                    <Text style={styles.taskTitle}>{item.title}</Text>
                    <Text style={styles.taskMeta}>{item.meta}</Text>
                  </View>
                </View>
                <View style={styles.taskFooter}>
                  <View style={[styles.pill, { backgroundColor: item.tagBg }]}>
                    <Text style={[styles.pillText, { color: item.tagFg }]}>{item.tagLabel}</Text>
                  </View>
                  {item.evidenceRequired ? <Text style={{ fontSize: 12 }}>📎</Text> : null}
                  <Text style={[styles.taskDue, { color: item.dueColor }]}>{item.due}</Text>
                </View>
              </TouchableOpacity>
            ))}
          </View>
        ))}

        {isAllClear ? (
          <View style={styles.emptyWrap}>
            <Text style={styles.emptyIcon}>🎉</Text>
            <Text style={styles.emptyLabel}>You're all caught up — no open tasks.</Text>
          </View>
        ) : null}
      </ScrollView>

      {/* FMS ticket detail sheet */}
      <BottomSheet visible={!!selectedFmsTicketId} onClose={closeFmsDetail}>
        {fmsDetail ? (
          <>
            <View style={styles.rowChips}>
              <Text style={styles.ticketDisplayId}>{fmsDetail.display_id}</Text>
              {fmsDetail.is_flagged ? <Text style={{ fontSize: 12 }}>🚩</Text> : null}
            </View>
            <Text style={styles.detailTitle}>{fmsDetail.title}</Text>
            <View style={styles.rowChips}>
              <View style={[styles.pill, { backgroundColor: PRIORITY_META[fmsDetail.priority].bg }]}>
                <Text style={[styles.pillText, { color: PRIORITY_META[fmsDetail.priority].fg }]}>{PRIORITY_META[fmsDetail.priority].label}</Text>
              </View>
              <View style={[styles.pill, { backgroundColor: (TICKET_STATUS_META[fmsDetail.status] ?? { bg: GRAY_BG }).bg }]}>
                <Text style={[styles.pillText, { color: (TICKET_STATUS_META[fmsDetail.status] ?? { fg: GRAY_FG }).fg }]}>
                  {TICKET_STATUS_META[fmsDetail.status]?.label ?? fmsDetail.status}
                </Text>
              </View>
            </View>

            <Text style={styles.sheetLabel}>Flow Progress</Text>
            {(fmsFlow?.stages ?? []).map((s) => {
              const isCurrent = s.id === fmsDetail.current_stage_id;
              const isDone = s.order < (fmsDetail.current_stage_order ?? 0);
              return (
                <View
                  key={s.id}
                  style={[
                    styles.stepperRow,
                    { backgroundColor: isCurrent ? s.color + "1a" : isDone ? "rgba(34,197,94,0.06)" : "#111827", borderColor: isCurrent ? s.color : isDone ? "rgba(34,197,94,0.25)" : "rgba(255,255,255,0.06)" },
                  ]}
                >
                  <View style={[styles.stepperDot, { backgroundColor: isDone ? GREEN : isCurrent ? s.color : "rgba(255,255,255,0.15)" }]} />
                  <Text style={[styles.stepperLabel, { color: isCurrent || isDone ? "#e2e8f0" : "#64748b" }]}>{s.name}</Text>
                  <Text style={styles.stepperBadge}>{isCurrent ? "● Now" : isDone ? "✓ Done" : "Upcoming"}</Text>
                </View>
              );
            })}

            {nextFmsStage ? (
              <TouchableOpacity style={styles.moveButton} onPress={advanceFmsTicket}>
                <Text style={styles.moveButtonText}>↗ Move to {nextFmsStage.name}</Text>
              </TouchableOpacity>
            ) : null}

            <Text style={[styles.sheetLabel, { marginTop: 20 }]}>Activity</Text>
            {fmsEvents.map((h, idx) => (
              <View key={idx} style={styles.activityRow}>
                <View style={styles.activityDot} />
                <View style={{ flex: 1 }}>
                  <Text style={styles.activityLabel}>{h.event_type.replace(/_/g, " ")}</Text>
                  <Text style={styles.activityMeta}>{h.actor_name} · {h.created_at}</Text>
                </View>
              </View>
            ))}
          </>
        ) : (
          <ActivityIndicator color={TEAL} />
        )}
      </BottomSheet>

      {/* Generic task detail sheet */}
      <BottomSheet visible={!!selectedTask} onClose={() => setSelectedTask(null)}>
        {selectedTask ? (
          <>
            <View style={styles.rowChips}>
              <View style={[styles.pill, { backgroundColor: selectedTask.statusBg }]}>
                <Text style={[styles.pillText, { color: selectedTask.statusFg }]}>{selectedTask.statusLabel}</Text>
              </View>
              <View style={[styles.pill, { backgroundColor: selectedTask.tagBg }]}>
                <Text style={[styles.pillText, { color: selectedTask.tagFg }]}>{selectedTask.tagLabel}</Text>
              </View>
            </View>
            <Text style={styles.detailTitle}>{selectedTask.title}</Text>
            <Text style={styles.detailDesc}>{selectedTask.description}</Text>

            <View style={styles.detailKV}>
              <View style={styles.detailKVRow}>
                <Text style={styles.detailKVLabel}>Due</Text>
                <Text style={styles.detailKVValue}>{selectedTask.due}</Text>
              </View>
              {selectedTask.evidenceRequired ? (
                <View style={styles.detailKVRow}>
                  <Text style={styles.detailKVLabel}>Evidence</Text>
                  <Text style={[styles.detailKVValue, { color: YELLOW }]}>📎 Required on completion</Text>
                </View>
              ) : null}
            </View>

            {canComplete(selectedTask) ? (
              <TouchableOpacity
                style={[styles.completeButton, { backgroundColor: selectedTask.overdue ? AMBER : TEAL }]}
                onPress={() => openComplete(selectedTask)}
              >
                <Text style={[styles.completeButtonText, { color: selectedTask.overdue ? "#1c1204" : "#0b0f1a" }]}>
                  {selectedTask.overdue ? "Mark Complete (Overdue)" : "Mark Complete"}
                </Text>
              </TouchableOpacity>
            ) : null}
          </>
        ) : null}
      </BottomSheet>

      {/* Complete sheet */}
      <BottomSheet visible={!!completingTask} onClose={closeComplete}>
        {completingTask ? (
          <>
            <Text style={styles.sheetTitleBig}>Complete task</Text>
            <Text style={styles.completeSubtitle}>{completingTask.title}</Text>

            {completingTask.evidenceRequired && completingTask.kind === "CHECKLIST" ? (
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

            <Text style={styles.sheetLabel}>{completingTask.overdue ? "Delay reason *" : "Note"}</Text>
            <TextInput
              style={[styles.input, completingTask.overdue && { borderColor: "rgba(245,158,11,0.3)" }]}
              placeholder={completingTask.overdue ? "Explain why this was completed late…" : "Optional note…"}
              placeholderTextColor="#64748b"
              value={completeNote}
              onChangeText={setCompleteNote}
              multiline
            />

            <View style={styles.sheetActions}>
              {completingTask.kind === "CHECKLIST" ? (
                <TouchableOpacity style={styles.failButton} onPress={markFailed} disabled={submitting}>
                  <Text style={styles.failButtonText}>Mark as Failed</Text>
                </TouchableOpacity>
              ) : null}
              <TouchableOpacity style={[styles.applyButton, submitting && { opacity: 0.6 }]} onPress={submitComplete} disabled={submitting}>
                {submitting ? <ActivityIndicator color="#0b0f1a" /> : <Text style={styles.applyButtonText}>Confirm Complete</Text>}
              </TouchableOpacity>
            </View>
          </>
        ) : null}
      </BottomSheet>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: "#0b0f1a" },
  topBar: { paddingTop: 54, paddingHorizontal: 20, paddingBottom: 10, flexDirection: "row", alignItems: "center", gap: 12 },
  iconButton: { width: 34, height: 34, borderRadius: 10, backgroundColor: "#111827", borderWidth: 1, borderColor: "rgba(255,255,255,0.08)", alignItems: "center", justifyContent: "center" },
  backIcon: { fontSize: 16, color: "#cbd5e1" },
  title: { fontSize: 17.5, fontWeight: "800", color: "#f1f5f9" },
  subtitle: { fontSize: 11, color: "#64748b", marginTop: 1 },
  error: { color: "#f87185", fontSize: 13, marginHorizontal: 20, marginTop: 8 },
  body: { flex: 1 },
  bodyContent: { paddingHorizontal: 20, paddingBottom: 30 },
  summaryGrid: { flexDirection: "row", gap: 8, marginBottom: 16 },
  summaryCard: { flex: 1, backgroundColor: "#111827", borderWidth: 1, borderColor: "rgba(255,255,255,0.08)", borderRadius: 12, padding: 12 },
  summaryValue: { fontSize: 20, fontWeight: "800", color: "#e2e8f0" },
  summaryLabel: { fontSize: 10.5, color: "#64748b", marginTop: 2 },
  sectionHeader: { flexDirection: "row", alignItems: "center", gap: 7, marginBottom: 10 },
  sectionLabel: { fontSize: 12, fontWeight: "800", color: "#94a3b8", textTransform: "uppercase", letterSpacing: 0.5 },
  sectionCount: { backgroundColor: "rgba(255,255,255,0.06)", borderRadius: 8, paddingHorizontal: 6, paddingVertical: 1 },
  sectionCountText: { fontSize: 10, fontWeight: "800", color: "#64748b" },
  ticketCard: { backgroundColor: "#111827", borderWidth: 1, borderColor: "rgba(255,255,255,0.08)", borderRadius: 14, padding: 14, marginBottom: 10 },
  ticketRow: { flexDirection: "row", alignItems: "flex-start", gap: 10 },
  rowChips: { flexDirection: "row", alignItems: "center", gap: 6, marginBottom: 3 },
  ticketDisplayId: { fontSize: 12, fontWeight: "700", color: "#60a5fa", fontFamily: "monospace" },
  ticketTitle: { fontSize: 13.5, fontWeight: "700", color: "#f1f5f9", marginTop: 1 },
  ticketMeta: { fontSize: 11.5, color: "#94a3b8", marginTop: 3 },
  pill: { paddingVertical: 3, paddingHorizontal: 8, borderRadius: 6 },
  pillText: { fontSize: 10.5, fontWeight: "700" },
  tatTrack: { height: 4, borderRadius: 2, backgroundColor: "rgba(255,255,255,0.08)", overflow: "hidden", marginTop: 10 },
  tatBar: { height: "100%", borderRadius: 2 },
  taskCard: { backgroundColor: "#111827", borderWidth: 1, borderColor: "rgba(255,255,255,0.08)", borderRadius: 14, padding: 15, marginBottom: 10 },
  taskRow: { flexDirection: "row", alignItems: "flex-start", gap: 10 },
  taskDot: { width: 8, height: 8, borderRadius: 4, marginTop: 5 },
  taskTitle: { fontSize: 14.5, fontWeight: "700", color: "#f1f5f9" },
  taskMeta: { fontSize: 12.5, color: "#94a3b8", marginTop: 2 },
  taskFooter: { flexDirection: "row", alignItems: "center", gap: 6, flexWrap: "wrap", marginTop: 11 },
  taskDue: { fontSize: 11.5, fontWeight: "600", marginLeft: "auto" },
  emptyWrap: { alignItems: "center", paddingTop: 60 },
  emptyIcon: { fontSize: 30, marginBottom: 10 },
  emptyLabel: { fontSize: 14, fontWeight: "600", color: "#94a3b8" },
  sheetLabel: { fontSize: 11, fontWeight: "700", color: "#64748b", textTransform: "uppercase", letterSpacing: 0.6, marginBottom: 8, marginTop: 4 },
  sheetTitleBig: { fontSize: 15, fontWeight: "800", color: "#f1f5f9", marginBottom: 4 },
  detailTitle: { fontSize: 17, fontWeight: "800", color: "#f1f5f9", marginBottom: 8 },
  detailDesc: { fontSize: 13.5, color: "#94a3b8", lineHeight: 19, marginBottom: 16 },
  detailKV: { borderTopWidth: 1, borderColor: "rgba(255,255,255,0.06)" },
  detailKVRow: { flexDirection: "row", justifyContent: "space-between", paddingVertical: 10, borderBottomWidth: 1, borderColor: "rgba(255,255,255,0.06)" },
  detailKVLabel: { fontSize: 12, fontWeight: "600", color: "#94a3b8" },
  detailKVValue: { fontSize: 12.5, fontWeight: "700", color: "#e2e8f0" },
  stepperRow: { flexDirection: "row", alignItems: "center", gap: 10, padding: 12, borderRadius: 10, borderWidth: 1, marginBottom: 6 },
  stepperDot: { width: 10, height: 10, borderRadius: 5 },
  stepperLabel: { flex: 1, fontSize: 12.5, fontWeight: "700" },
  stepperBadge: { fontSize: 10.5, color: "#64748b" },
  moveButton: { marginTop: 12, height: 50, borderRadius: 12, backgroundColor: TEAL, alignItems: "center", justifyContent: "center" },
  moveButtonText: { fontSize: 15, fontWeight: "700", color: "#0b0f1a" },
  activityRow: { flexDirection: "row", gap: 10, paddingBottom: 12 },
  activityDot: { width: 8, height: 8, borderRadius: 4, backgroundColor: BLUE, marginTop: 5 },
  activityLabel: { fontSize: 11.5, fontWeight: "700", color: "#cbd5e1", textTransform: "capitalize" },
  activityMeta: { fontSize: 10.5, color: "#64748b", marginTop: 1 },
  completeButton: { marginTop: 16, height: 50, borderRadius: 12, alignItems: "center", justifyContent: "center" },
  completeButtonText: { fontSize: 15, fontWeight: "700" },
  completeSubtitle: { fontSize: 13, color: "#94a3b8", marginBottom: 16 },
  input: { width: "100%", height: 60, borderRadius: 12, backgroundColor: "#0d1424", borderWidth: 1, borderColor: "rgba(255,255,255,0.1)", color: "#e2e8f0", fontSize: 14, paddingHorizontal: 14, paddingTop: 12, marginBottom: 4, textAlignVertical: "top" },
  evidenceAttached: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", height: 46, borderRadius: 10, backgroundColor: "rgba(45,212,191,0.1)", borderWidth: 1, borderColor: "rgba(45,212,191,0.3)", paddingHorizontal: 14, marginBottom: 14 },
  evidenceAttachedText: { fontSize: 13.5, fontWeight: "600", color: TEAL },
  evidenceEmpty: { height: 46, borderRadius: 10, borderWidth: 1, borderColor: "rgba(255,255,255,0.15)", borderStyle: "dashed", alignItems: "center", justifyContent: "center", marginBottom: 14 },
  evidenceEmptyText: { fontSize: 13.5, fontWeight: "600", color: "#94a3b8" },
  sheetActions: { flexDirection: "row", gap: 10, marginTop: 12 },
  failButton: { flex: 1, height: 50, borderRadius: 12, borderWidth: 1.5, borderColor: "rgba(239,68,68,0.4)", backgroundColor: "rgba(239,68,68,0.08)", alignItems: "center", justifyContent: "center" },
  failButtonText: { fontSize: 13.5, fontWeight: "700", color: "#f87171" },
  applyButton: { flex: 2, height: 50, borderRadius: 12, backgroundColor: TEAL, alignItems: "center", justifyContent: "center" },
  applyButtonText: { fontSize: 15, fontWeight: "700", color: "#0b0f1a" },
});
