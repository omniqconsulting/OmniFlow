import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Linking,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import * as ImagePicker from "expo-image-picker";

import type { AuthStackParamList } from "../navigation/AuthNavigator";
import { API_BASE_URL, ApiError } from "../api/client";
import { formatIstDateTime } from "../utils/dateFormat";
import BottomSheet from "../components/BottomSheet";
import EmployeePicker from "../components/EmployeePicker";
import LinkedEntityPicker from "../components/LinkedEntityPicker";
import {
  acknowledgeTicket,
  addHelper,
  addLinkedEntity,
  deleteTicket,
  flagTicket,
  getLinkedEntityOptions,
  getTicket,
  listAttachments,
  listComments,
  listEmployeeOptions,
  listEvents,
  listHelpers,
  listLinkedEntities,
  logDelay,
  postComment,
  removeHelper,
  unflagTicket,
  updateTicket,
  uploadAttachment,
  uploadEvidence,
  type EmployeeOption,
  type LinkedEntity,
  type LinkedEntityOption,
  type Ticket,
  type TicketAttachment,
  type TicketComment,
  type TicketEvent,
  type TicketHelper,
  type TicketPriority,
} from "../api/tickets";

const TEAL = "#2DD4BF";

type Props = NativeStackScreenProps<AuthStackParamList, "TicketDetail">;

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

const EVENT_LABEL: Record<string, string> = {
  CREATED: "created this ticket",
  STATUS_CHANGED: "changed the status",
  COMMENTED: "commented",
  PROOF_UPLOADED: "uploaded evidence",
  ACKNOWLEDGED: "acknowledged this ticket",
  REASSIGNED: "reassigned this ticket",
  FLAGGED: "flagged this ticket",
  FLAG_REMOVED: "removed the flag",
  DELAY_LOGGED: "logged a delay",
};

function relTime(iso: string): string {
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return "Just now";
  const m = Math.floor(seconds / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

const fmtDue = formatIstDateTime;

type Sheet = null | "overflow" | "delay" | "reassign" | "flag" | "edit" | "evidence" | "delete";

export default function TicketDetailScreen({ navigation, route }: Props) {
  const { user, ticketId } = route.params;
  const canManage = user.role === "ADMIN" || user.role === "MANAGER";

  const [ticket, setTicket] = useState<Ticket | null>(null);
  const [events, setEvents] = useState<TicketEvent[]>([]);
  const [comments, setComments] = useState<TicketComment[]>([]);
  const [attachments, setAttachments] = useState<TicketAttachment[]>([]);
  const [linkedEntities, setLinkedEntities] = useState<LinkedEntity[]>([]);
  const [helpers, setHelpers] = useState<TicketHelper[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [sheet, setSheet] = useState<Sheet>(null);
  const [reassignPickerOpen, setReassignPickerOpen] = useState(false);
  const [helperPickerOpen, setHelperPickerOpen] = useState(false);
  const [linkPickerOpen, setLinkPickerOpen] = useState(false);
  const [linkOptions, setLinkOptions] = useState<LinkedEntityOption[]>([]);

  const [commentText, setCommentText] = useState("");
  const [delayReason, setDelayReason] = useState("");
  const [flagReason, setFlagReason] = useState("");
  const [employees, setEmployees] = useState<EmployeeOption[]>([]);
  const [editTitle, setEditTitle] = useState("");
  const [editDesc, setEditDesc] = useState("");
  const [editPriority, setEditPriority] = useState<TicketPriority>("MEDIUM");

  const load = useCallback(async () => {
    try {
      const [t, ev, cm, at, le, hp] = await Promise.all([
        getTicket(ticketId), listEvents(ticketId), listComments(ticketId),
        listAttachments(ticketId), listLinkedEntities(ticketId), listHelpers(ticketId),
      ]);
      setTicket(t);
      setEvents(ev);
      setComments(cm);
      setAttachments(at);
      setLinkedEntities(le);
      setHelpers(hp);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Couldn't load this ticket.");
    } finally {
      setLoading(false);
    }
  }, [ticketId]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (canManage && (sheet === "reassign" || reassignPickerOpen || helperPickerOpen) && employees.length === 0) {
      listEmployeeOptions().then(setEmployees).catch(() => {});
    }
  }, [canManage, sheet, reassignPickerOpen, helperPickerOpen, employees.length]);

  useEffect(() => {
    if (linkPickerOpen && linkOptions.length === 0) {
      getLinkedEntityOptions().then(setLinkOptions).catch(() => {});
    }
  }, [linkPickerOpen, linkOptions.length]);

  if (loading) {
    return (
      <View style={[styles.screen, { alignItems: "center", justifyContent: "center" }]}>
        <ActivityIndicator color={TEAL} />
      </View>
    );
  }
  if (error || !ticket) {
    return (
      <View style={[styles.screen, { alignItems: "center", justifyContent: "center", padding: 20 }]}>
        <Text style={styles.error}>{error ?? "Ticket not found."}</Text>
        <TouchableOpacity style={styles.backLink} onPress={() => navigation.goBack()}>
          <Text style={{ color: TEAL, fontWeight: "700" }}>Go back</Text>
        </TouchableOpacity>
      </View>
    );
  }

  const isAssignee = ticket.current_assignee_id === user.id;
  const canDoPrimary = ticket.status === "OPEN" && (isAssignee || canManage);
  const overdue = !!ticket.due_at && new Date(ticket.due_at) < new Date() && ticket.status === "OPEN";

  const ackState: "done" | "pending" | "skipped" = ticket.acknowledged_at ? "done" : (ticket.status === "DONE" || ticket.status === "CLOSED" ? "skipped" : "pending");
  const doneState: "done" | "current" | "pending" = ticket.status === "DONE" || ticket.status === "CLOSED" ? "done" : (ackState !== "pending" ? "current" : "pending");
  const closedState: "done" | "current" | "pending" = ticket.status === "CLOSED" ? "done" : (ticket.status === "DONE" ? "current" : "pending");
  const steps: { label: string; mark: string; state: "done" | "current" | "pending" | "skipped" }[] = [
    { label: "Open", mark: "✓", state: "done" },
    { label: "Ack", mark: ackState === "done" ? "✓" : ackState === "skipped" ? "–" : "2", state: ackState },
    { label: "Done", mark: doneState === "done" ? "✓" : "3", state: doneState },
    { label: "Closed", mark: closedState === "done" ? "✓" : "4", state: closedState },
  ];
  const stepColor = (state: string) =>
    state === "done" ? "#2DD4BF" : state === "current" ? "#a99cf7" : state === "skipped" ? "#64748b" : "#475569";
  const stepBg = (state: string) =>
    state === "done" ? "rgba(45,212,191,0.18)" : state === "current" ? "rgba(102,87,242,0.18)" : "rgba(148,163,184,0.12)";

  const primaryLabel = canDoPrimary
    ? (ticket.evidence_required ? "📎 Submit with Evidence" : "✓ Mark as Done")
    : (ticket.status === "DONE" && canManage) ? "Close Ticket"
    : (ticket.status === "CLOSED" && user.role === "ADMIN") ? "Reopen"
    : null;

  const runAction = async (fn: () => Promise<Ticket>) => {
    setBusy(true);
    try {
      const updated = await fn();
      setTicket(updated);
      const ev = await listEvents(ticketId);
      setEvents(ev);
      setSheet(null);
    } catch (e) {
      Alert.alert("Action failed", e instanceof ApiError ? e.detail : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  };

  const onPrimary = () => {
    if (busy) return;
    if (canDoPrimary) {
      if (ticket.evidence_required) {
        setSheet("evidence");
      } else {
        runAction(() => updateTicket(ticketId, { status: "DONE" }));
      }
    } else if (ticket.status === "DONE" && canManage) {
      runAction(() => updateTicket(ticketId, { status: "CLOSED" }));
    } else if (ticket.status === "CLOSED" && user.role === "ADMIN") {
      runAction(() => updateTicket(ticketId, { status: "OPEN" }));
    }
  };

  const submitEvidence = async () => {
    setBusy(true);
    try {
      const perm = await ImagePicker.requestCameraPermissionsAsync();
      if (!perm.granted) {
        Alert.alert("Camera access needed", "Enable camera access to attach evidence.");
        setBusy(false);
        return;
      }
      const result = await ImagePicker.launchCameraAsync({ quality: 0.6 });
      if (result.canceled || !result.assets?.[0]) {
        setBusy(false);
        return;
      }
      await uploadEvidence(ticketId, result.assets[0].uri);
      const updated = await updateTicket(ticketId, { status: "DONE" });
      setTicket(updated);
      const ev = await listEvents(ticketId);
      setEvents(ev);
      setSheet(null);
    } catch (e) {
      Alert.alert("Couldn't submit evidence", e instanceof ApiError ? e.detail : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  };

  const submitCommentAction = async () => {
    if (!commentText.trim() || busy) return;
    setBusy(true);
    try {
      await postComment(ticketId, commentText.trim());
      setCommentText("");
      const [ev, cm] = await Promise.all([listEvents(ticketId), listComments(ticketId)]);
      setEvents(ev);
      setComments(cm);
    } catch (e) {
      Alert.alert("Couldn't post comment", e instanceof ApiError ? e.detail : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  };

  const addAttachment = async (fromCamera: boolean) => {
    setBusy(true);
    try {
      if (fromCamera) {
        const perm = await ImagePicker.requestCameraPermissionsAsync();
        if (!perm.granted) {
          Alert.alert("Camera access needed", "Enable camera access to attach a document.");
          return;
        }
      } else {
        const perm = await ImagePicker.requestMediaLibraryPermissionsAsync();
        if (!perm.granted) {
          Alert.alert("Photo access needed", "Enable photo library access to attach a document.");
          return;
        }
      }
      const result = fromCamera
        ? await ImagePicker.launchCameraAsync({ quality: 0.6 })
        : await ImagePicker.launchImageLibraryAsync({ quality: 0.6 });
      if (result.canceled || !result.assets?.[0]) return;
      await uploadAttachment(ticketId, result.assets[0].uri, "attachment.jpg");
      const at = await listAttachments(ticketId);
      setAttachments(at);
    } catch (e) {
      Alert.alert("Couldn't upload attachment", e instanceof ApiError ? e.detail : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  };

  const pickAttachmentSource = () => {
    Alert.alert("Add attachment", "Attach a photo of a supporting document.", [
      { text: "Take Photo", onPress: () => addAttachment(true) },
      { text: "Choose from Library", onPress: () => addAttachment(false) },
      { text: "Cancel", style: "cancel" },
    ]);
  };

  const openEdit = () => {
    setEditTitle(ticket.title);
    setEditDesc(ticket.description);
    setEditPriority(ticket.priority);
    setSheet("edit");
  };

  const overflowItems: { icon: string; label: string; danger?: boolean; onPress: () => void }[] = [];
  if (ticket.status === "OPEN" && isAssignee) {
    overflowItems.push({ icon: "⏱", label: "Log a Delay", onPress: () => setSheet("delay") });
  }
  if (ticket.status === "OPEN" && (isAssignee || canManage)) {
    overflowItems.push({ icon: "↩", label: "Reassign", onPress: () => setSheet("reassign") });
  }
  if (canManage && ticket.status !== "CLOSED") {
    overflowItems.push({ icon: "✏", label: "Edit Ticket", onPress: openEdit });
  }
  if (canManage && ticket.status !== "CLOSED") {
    overflowItems.push({ icon: "🧑‍🤝‍🧑", label: "Add Helper", onPress: () => { setSheet(null); setHelperPickerOpen(true); } });
  }
  if (canManage || isAssignee) {
    if (!ticket.is_flagged) overflowItems.push({ icon: "🚩", label: "Flag Ticket", onPress: () => setSheet("flag") });
    else if (canManage) overflowItems.push({ icon: "🚩", label: "Remove Flag", onPress: () => runAction(() => unflagTicket(ticketId)) });
  }
  if (user.role === "ADMIN" && ticket.status === "OPEN") {
    overflowItems.push({ icon: "🗑", label: "Delete", danger: true, onPress: () => setSheet("delete") });
  }

  return (
    <View style={styles.screen}>
      <View style={styles.topBar}>
        <TouchableOpacity style={styles.iconButton} onPress={() => navigation.goBack()}>
          <Text style={styles.iconButtonText}>‹</Text>
        </TouchableOpacity>
        <Text style={styles.topBarId}>{ticket.display_id}</Text>
        <TouchableOpacity style={styles.iconButton} onPress={() => setSheet("overflow")}>
          <Text style={styles.iconButtonText}>⋯</Text>
        </TouchableOpacity>
      </View>

      <ScrollView style={styles.body} contentContainerStyle={{ padding: 20, paddingBottom: 110 }}>
        <Text style={styles.title}>{ticket.is_flagged ? "🚩 " : ""}{ticket.title}</Text>
        <View style={styles.badgeRow}>
          {ticket.ticket_category === "HELP" ? (
            <View style={[styles.badge, styles.helpBadge]}><Text style={[styles.badgeText, { color: "#eab308" }]}>HELP</Text></View>
          ) : null}
          <View style={[styles.badge, { backgroundColor: PRIORITY_META[ticket.priority].color + "26" }]}>
            <Text style={[styles.badgeText, { color: PRIORITY_META[ticket.priority].color }]}>{PRIORITY_META[ticket.priority].label}</Text>
          </View>
          <View style={[styles.badge, { backgroundColor: STATUS_META[ticket.status].bg }]}>
            <Text style={[styles.badgeText, { color: STATUS_META[ticket.status].fg }]}>{STATUS_META[ticket.status].label}</Text>
          </View>
          {ticket.evidence_required ? (
            <View style={[styles.badge, styles.helpBadge]}><Text style={[styles.badgeText, { color: "#eab308" }]}>Evidence Required</Text></View>
          ) : null}
        </View>

        <View style={styles.stepsRow}>
          {steps.map((s) => (
            <View key={s.label} style={styles.stepItem}>
              <View style={[styles.stepDot, { backgroundColor: stepBg(s.state) }]}>
                <Text style={{ color: stepColor(s.state), fontSize: 10.5, fontWeight: "700" }}>{s.mark}</Text>
              </View>
              <Text style={{ fontSize: 9.5, fontWeight: "600", color: s.state === "pending" ? "#475569" : "#94a3b8" }}>{s.label}</Text>
            </View>
          ))}
        </View>

        <View style={styles.metaGrid}>
          <View style={styles.metaCell}><Text style={styles.metaLabel}>Assignee</Text><Text style={styles.metaValue}>{ticket.assignee_name ?? "—"}</Text></View>
          <View style={styles.metaCell}><Text style={styles.metaLabel}>Due</Text><Text style={[styles.metaValue, overdue && { color: "#fb7185" }]}>{fmtDue(ticket.due_at)}{overdue ? " · Overdue" : ""}</Text></View>
          <View style={styles.metaCell}><Text style={styles.metaLabel}>Created by</Text><Text style={styles.metaValue}>{ticket.created_by_name ?? "—"}</Text></View>
          <View style={styles.metaCell}><Text style={styles.metaLabel}>Acked</Text><Text style={styles.metaValue}>{ticket.acknowledged_at ? relTime(ticket.acknowledged_at) : "—"}</Text></View>
        </View>

        {ticket.is_flagged && ticket.flagged_reason ? (
          <Text style={styles.flagNote}>🚩 {ticket.flagged_reason}</Text>
        ) : null}

        {(isAssignee || canManage) && !ticket.acknowledged_at && ticket.status !== "CLOSED" ? (
          <TouchableOpacity style={styles.ackButton} onPress={() => runAction(() => acknowledgeTicket(ticketId))} disabled={busy}>
            <Text style={styles.ackButtonText}>✓ Acknowledge</Text>
          </TouchableOpacity>
        ) : null}

        <View style={styles.card}>
          <Text style={styles.cardTitle}>Description</Text>
          <Text style={styles.cardBody}>{ticket.description}</Text>
        </View>

        <View style={styles.card}>
          <View style={styles.cardHeaderRow}>
            <Text style={styles.cardTitle}>🔗 Linked Records</Text>
          </View>
          {linkedEntities.length === 0 ? (
            <Text style={styles.mutedText}>No linked entities yet.</Text>
          ) : (
            <View style={styles.linkChips}>
              {linkedEntities.map((ref) => (
                <View key={ref.id} style={styles.linkChip}>
                  <Text style={styles.linkChipType}>
                    {ref.entity_type === "CUSTOM_LIST" ? "Ref" : ref.entity_type === "OTHER" ? "Custom" : ref.entity_type.replace(/_/g, " ")}
                  </Text>
                  <Text style={styles.linkChipLabel}>{ref.entity_label || ref.custom_text || "—"}</Text>
                </View>
              ))}
            </View>
          )}
          {canManage ? (
            <TouchableOpacity style={styles.dashedButton} onPress={() => setLinkPickerOpen(true)}>
              <Text style={styles.dashedButtonText}>+ Add linked record</Text>
            </TouchableOpacity>
          ) : null}
        </View>

        <View style={styles.card}>
          <View style={styles.cardHeaderRow}>
            <Text style={styles.cardTitle}>🧑‍🤝‍🧑 Helpers</Text>
          </View>
          {helpers.length === 0 ? (
            <Text style={styles.mutedText}>No helpers added yet.</Text>
          ) : (
            helpers.map((h) => (
              <View key={h.id} style={styles.helperRow}>
                <Text style={styles.helperName}>{h.user_name ?? "Unknown"}</Text>
                {h.note ? <Text style={styles.helperNote}>{h.note}</Text> : null}
                {canManage ? (
                  <TouchableOpacity
                    onPress={() =>
                      runAction(async () => {
                        await removeHelper(ticketId, h.user_id);
                        const hp = await listHelpers(ticketId);
                        setHelpers(hp);
                        return ticket;
                      })
                    }
                  >
                    <Text style={styles.linkChipRemove}>✕</Text>
                  </TouchableOpacity>
                ) : null}
              </View>
            ))
          )}
          {canManage && ticket.status !== "CLOSED" ? (
            <TouchableOpacity style={styles.dashedButton} onPress={() => setHelperPickerOpen(true)}>
              <Text style={styles.dashedButtonText}>+ Add helper</Text>
            </TouchableOpacity>
          ) : null}
        </View>

        <View style={styles.card}>
          <View style={styles.cardHeaderRow}>
            <Text style={styles.cardTitle}>📎 Attachments</Text>
          </View>
          {attachments.length === 0 ? (
            <Text style={styles.mutedText}>No attachments yet.</Text>
          ) : (
            attachments.map((a) => (
              <TouchableOpacity key={a.id} style={styles.attachmentRow} onPress={() => Linking.openURL(`${API_BASE_URL}${a.file_path}`)}>
                <Text style={{ fontSize: 15 }}>🖼️</Text>
                <Text style={styles.attachmentName} numberOfLines={1}>{a.file_name}</Text>
              </TouchableOpacity>
            ))
          )}
          <TouchableOpacity style={styles.dashedButton} onPress={pickAttachmentSource} disabled={busy}>
            <Text style={styles.dashedButtonText}>+ Add supporting document</Text>
          </TouchableOpacity>
        </View>

        <View style={styles.card}>
          <Text style={styles.cardTitle}>Ticket Journey</Text>
          {events.map((ev) => (
            <View key={ev.id} style={styles.journeyRow}>
              <View style={styles.journeyDot} />
              <View style={{ flex: 1 }}>
                <Text style={styles.journeyText}>
                  <Text style={{ fontWeight: "700", color: "#e2e8f0" }}>{ev.actor_name ?? "Someone"}</Text>{" "}
                  {EVENT_LABEL[ev.event_type] ?? ev.event_type.replace(/_/g, " ").toLowerCase()}
                  {ev.detail ? ` — ${ev.detail}` : ""}
                </Text>
                <Text style={styles.journeyWhen}>{relTime(ev.created_at)}</Text>
              </View>
            </View>
          ))}
        </View>

        <View style={styles.card}>
          <Text style={styles.cardTitle}>Comments</Text>
          {comments.map((c) => (
            <View key={c.id} style={styles.journeyRow}>
              <View style={styles.journeyDot} />
              <View style={{ flex: 1 }}>
                <Text style={styles.journeyText}>
                  <Text style={{ fontWeight: "700", color: "#e2e8f0" }}>{c.user_name ?? "Someone"}</Text> {c.body}
                </Text>
                <Text style={styles.journeyWhen}>{relTime(c.created_at)}</Text>
              </View>
            </View>
          ))}
          <TextInput
            style={[styles.input, { height: 60, textAlignVertical: "top", marginTop: comments.length ? 12 : 4 }]}
            placeholder="Write a comment…"
            placeholderTextColor="#64748b"
            value={commentText}
            onChangeText={setCommentText}
            multiline
          />
          <TouchableOpacity style={styles.postButton} onPress={submitCommentAction} disabled={busy || !commentText.trim()}>
            <Text style={styles.postButtonText}>Post →</Text>
          </TouchableOpacity>
        </View>
      </ScrollView>

      <View style={styles.bottomBar}>
        <TouchableOpacity style={styles.overflowButton} onPress={() => setSheet("overflow")}>
          <Text style={{ fontSize: 17, color: "#cbd5e1" }}>⋯</Text>
        </TouchableOpacity>
        {primaryLabel ? (
          <TouchableOpacity
            style={[
              styles.primaryButton,
              { backgroundColor: canDoPrimary ? "#10b981" : ticket.status === "DONE" && canManage ? "#334155" : "#1e293b" },
              ticket.status === "CLOSED" && user.role !== "ADMIN" && { opacity: 0.5 },
            ]}
            onPress={onPrimary}
            disabled={busy}
          >
            {busy ? <ActivityIndicator color="#fff" /> : <Text style={styles.primaryButtonText}>{primaryLabel}</Text>}
          </TouchableOpacity>
        ) : (
          <View style={[styles.primaryButton, { backgroundColor: "#1e293b", opacity: 0.5 }]}>
            <Text style={styles.primaryButtonText}>—</Text>
          </View>
        )}
      </View>

      <BottomSheet visible={sheet !== null} onClose={() => setSheet(null)}>
            {sheet === "overflow" ? (
              <>
                <Text style={styles.modalTitle}>More Actions</Text>
                {overflowItems.length === 0 ? <Text style={styles.cardBody}>No actions available.</Text> : null}
                {overflowItems.map((item) => (
                  <TouchableOpacity key={item.label} style={styles.overflowItem} onPress={item.onPress}>
                    <Text style={{ marginRight: 10, fontSize: 15 }}>{item.icon}</Text>
                    <Text style={[styles.overflowItemText, item.danger && { color: "#fb7185" }]}>{item.label}</Text>
                  </TouchableOpacity>
                ))}
              </>
            ) : null}

            {sheet === "delay" ? (
              <>
                <Text style={styles.modalTitle}>Log a Delay</Text>
                <TextInput
                  style={[styles.input, { height: 76, textAlignVertical: "top" }]}
                  placeholder="Describe why this ticket is delayed…"
                  placeholderTextColor="#64748b"
                  value={delayReason}
                  onChangeText={setDelayReason}
                  multiline
                />
                <TouchableOpacity
                  style={[styles.modalSubmit, { marginTop: 14 }]}
                  onPress={() => delayReason.trim() && runAction(() => logDelay(ticketId, delayReason.trim()))}
                  disabled={busy || !delayReason.trim()}
                >
                  {busy ? <ActivityIndicator color="#fff" /> : <Text style={styles.modalSubmitText}>Submit Delay Log</Text>}
                </TouchableOpacity>
              </>
            ) : null}

            {sheet === "reassign" ? (
              <>
                <Text style={styles.modalTitle}>Reassign Ticket</Text>
                <Text style={styles.label}>New assignee *</Text>
                <TouchableOpacity style={styles.input} onPress={() => setReassignPickerOpen(true)}>
                  <Text style={{ color: "#e2e8f0" }}>Select employee…</Text>
                </TouchableOpacity>
              </>
            ) : null}

            {sheet === "flag" ? (
              <>
                <Text style={styles.modalTitle}>Flag Ticket</Text>
                <TextInput
                  style={styles.input}
                  placeholder="e.g. Missed deadline, quality issue…"
                  placeholderTextColor="#64748b"
                  value={flagReason}
                  onChangeText={setFlagReason}
                />
                <TouchableOpacity
                  style={[styles.dangerSubmit, { marginTop: 14 }]}
                  onPress={() => flagReason.trim() && runAction(() => flagTicket(ticketId, flagReason.trim()))}
                  disabled={busy || !flagReason.trim()}
                >
                  {busy ? <ActivityIndicator color="#fff" /> : <Text style={styles.modalSubmitText}>Confirm Flag</Text>}
                </TouchableOpacity>
              </>
            ) : null}

            {sheet === "edit" ? (
              <>
                <Text style={styles.modalTitle}>Edit Ticket</Text>
                <Text style={styles.label}>Title</Text>
                <TextInput style={styles.input} value={editTitle} onChangeText={setEditTitle} />
                <Text style={styles.label}>Description</Text>
                <TextInput style={[styles.input, { height: 60, textAlignVertical: "top" }]} value={editDesc} onChangeText={setEditDesc} multiline />
                <Text style={styles.label}>Priority</Text>
                <View style={styles.chipRow}>
                  {(["LOW", "MEDIUM", "HIGH", "CRITICAL"] as const).map((p) => (
                    <TouchableOpacity
                      key={p}
                      style={[styles.priorityChip, editPriority === p && { borderColor: PRIORITY_META[p].color, backgroundColor: PRIORITY_META[p].color + "26" }]}
                      onPress={() => setEditPriority(p)}
                    >
                      <Text style={[styles.priorityChipText, editPriority === p && { color: PRIORITY_META[p].color }]}>{PRIORITY_META[p].label}</Text>
                    </TouchableOpacity>
                  ))}
                </View>
                <TouchableOpacity
                  style={[styles.modalSubmit, { marginTop: 14 }]}
                  onPress={() => runAction(() => updateTicket(ticketId, { title: editTitle.trim(), description: editDesc.trim(), priority: editPriority }))}
                  disabled={busy || !editTitle.trim() || !editDesc.trim()}
                >
                  {busy ? <ActivityIndicator color="#fff" /> : <Text style={styles.modalSubmitText}>Save Changes</Text>}
                </TouchableOpacity>
              </>
            ) : null}

            {sheet === "evidence" ? (
              <>
                <Text style={styles.modalTitle}>Submit with Evidence</Text>
                <Text style={styles.cardBody}>An evidence photo is required before this ticket can be marked Done.</Text>
                <TouchableOpacity style={[styles.modalSubmit, { marginTop: 14 }]} onPress={submitEvidence} disabled={busy}>
                  {busy ? <ActivityIndicator color="#fff" /> : <Text style={styles.modalSubmitText}>📎 Take Photo & Mark Done</Text>}
                </TouchableOpacity>
              </>
            ) : null}

            {sheet === "delete" ? (
              <>
                <Text style={styles.modalTitle}>Delete Ticket?</Text>
                <Text style={styles.cardBody}>This soft-deletes the ticket — hidden but retained. This cannot be undone from here.</Text>
                <View style={styles.modalActions}>
                  <TouchableOpacity style={styles.modalCancel} onPress={() => setSheet(null)}>
                    <Text style={styles.modalCancelText}>Cancel</Text>
                  </TouchableOpacity>
                  <TouchableOpacity
                    style={styles.dangerSubmitFlex}
                    onPress={async () => {
                      setBusy(true);
                      try {
                        await deleteTicket(ticketId);
                        navigation.goBack();
                      } catch (e) {
                        Alert.alert("Couldn't delete", e instanceof ApiError ? e.detail : "Something went wrong.");
                      } finally {
                        setBusy(false);
                      }
                    }}
                    disabled={busy}
                  >
                    {busy ? <ActivityIndicator color="#fff" /> : <Text style={styles.modalSubmitText}>Delete</Text>}
                  </TouchableOpacity>
                </View>
              </>
            ) : null}

            <TouchableOpacity style={styles.sheetCloseLink} onPress={() => setSheet(null)}>
              <Text style={{ color: "#64748b", fontSize: 12 }}>Cancel</Text>
            </TouchableOpacity>
      </BottomSheet>

      <EmployeePicker
        visible={reassignPickerOpen}
        title="Reassign Ticket"
        employees={employees}
        excludeId={ticket.current_assignee_id ?? undefined}
        onSelect={(e) => {
          setReassignPickerOpen(false);
          runAction(() => updateTicket(ticketId, { assigneeId: e.id }));
        }}
        onClose={() => setReassignPickerOpen(false)}
      />

      <EmployeePicker
        visible={helperPickerOpen}
        title="Add Helper"
        employees={employees}
        excludeId={ticket.current_assignee_id ?? undefined}
        onSelect={(e) => {
          setHelperPickerOpen(false);
          runAction(async () => {
            await addHelper(ticketId, e.id);
            const hp = await listHelpers(ticketId);
            setHelpers(hp);
            return ticket;
          });
        }}
        onClose={() => setHelperPickerOpen(false)}
      />

      <LinkedEntityPicker
        visible={linkPickerOpen}
        options={linkOptions}
        onSelect={async (sel) => {
          try {
            await addLinkedEntity(ticketId, sel);
            const le = await listLinkedEntities(ticketId);
            setLinkedEntities(le);
          } catch (e) {
            Alert.alert("Couldn't link record", e instanceof ApiError ? e.detail : "Something went wrong.");
          }
        }}
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
  iconButton: {
    width: 34, height: 34, borderRadius: 10, backgroundColor: "#111827",
    borderWidth: 1, borderColor: "rgba(255,255,255,0.08)", alignItems: "center", justifyContent: "center",
  },
  iconButtonText: { fontSize: 17, color: "#cbd5e1" },
  topBarId: { fontSize: 13.5, fontWeight: "700", color: "#64748b", letterSpacing: 0.4 },
  error: { color: "#f87185", fontSize: 14, textAlign: "center" },
  backLink: { marginTop: 14 },
  body: { flex: 1 },
  title: { fontSize: 18, fontWeight: "800", color: "#f1f5f9", marginTop: 4 },
  badgeRow: { flexDirection: "row", gap: 6, flexWrap: "wrap", marginTop: 8 },
  badge: { paddingHorizontal: 8, paddingVertical: 3, borderRadius: 6 },
  badgeText: { fontSize: 11, fontWeight: "700" },
  helpBadge: { backgroundColor: "rgba(234,179,8,0.16)" },
  stepsRow: {
    flexDirection: "row", alignItems: "center", marginTop: 20, backgroundColor: "#111827",
    borderWidth: 1, borderColor: "rgba(255,255,255,0.08)", borderRadius: 14, paddingVertical: 16,
  },
  stepItem: { flex: 1, alignItems: "center", gap: 6 },
  stepDot: { width: 22, height: 22, borderRadius: 11, alignItems: "center", justifyContent: "center" },
  metaGrid: { flexDirection: "row", flexWrap: "wrap", gap: 12, marginTop: 16 },
  metaCell: { width: "47%" },
  metaLabel: { fontSize: 11, color: "#64748b", textTransform: "uppercase", letterSpacing: 0.4 },
  metaValue: { fontSize: 14, fontWeight: "600", color: "#e2e8f0", marginTop: 3 },
  flagNote: {
    marginTop: 12, fontSize: 13, color: "#fb7185", backgroundColor: "rgba(244,63,94,0.1)",
    borderWidth: 1, borderColor: "rgba(244,63,94,0.25)", borderRadius: 10, padding: 10,
  },
  ackButton: {
    marginTop: 14, height: 46, borderRadius: 12, borderWidth: 1, borderColor: "rgba(45,212,191,0.35)",
    alignItems: "center", justifyContent: "center",
  },
  ackButtonText: { color: TEAL, fontSize: 14, fontWeight: "700" },
  card: {
    marginTop: 14, backgroundColor: "#111827", borderWidth: 1, borderColor: "rgba(255,255,255,0.08)",
    borderRadius: 14, padding: 16,
  },
  cardTitle: { fontSize: 14, fontWeight: "700", color: "#f1f5f9", marginBottom: 8 },
  cardBody: { fontSize: 13.5, color: "#cbd5e1", lineHeight: 20 },
  cardHeaderRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  mutedText: { fontSize: 13, color: "#64748b" },
  linkChips: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  linkChip: {
    backgroundColor: "#1a2333", borderWidth: 1, borderColor: "rgba(255,255,255,0.08)",
    borderRadius: 8, paddingHorizontal: 10, paddingVertical: 7,
  },
  linkChipType: { fontSize: 10, color: "#64748b", textTransform: "uppercase", letterSpacing: 0.4 },
  linkChipLabel: { fontSize: 13, color: "#e2e8f0", fontWeight: "600", marginTop: 2 },
  linkChipRemove: { fontSize: 14, color: "#fb7185", paddingHorizontal: 4 },
  attachmentRow: {
    flexDirection: "row", alignItems: "center", gap: 8, paddingVertical: 9,
    borderTopWidth: 1, borderColor: "rgba(255,255,255,0.06)",
  },
  attachmentName: { fontSize: 13.5, color: "#93c5fd", flex: 1 },
  helperRow: {
    flexDirection: "row", alignItems: "center", gap: 8, paddingVertical: 9,
    borderTopWidth: 1, borderColor: "rgba(255,255,255,0.06)",
  },
  helperName: { fontSize: 14, fontWeight: "600", color: "#e2e8f0" },
  helperNote: { fontSize: 12.5, color: "#64748b", flex: 1 },
  dashedButton: {
    height: 42, borderRadius: 10, borderWidth: 1, borderColor: "rgba(255,255,255,0.12)", borderStyle: "dashed",
    alignItems: "center", justifyContent: "center", marginTop: 10,
  },
  dashedButtonText: { fontSize: 13, fontWeight: "600", color: "#94a3b8" },
  journeyRow: { flexDirection: "row", gap: 10, paddingVertical: 8, borderTopWidth: 1, borderColor: "rgba(255,255,255,0.06)" },
  journeyDot: { width: 6, height: 6, borderRadius: 3, backgroundColor: TEAL, marginTop: 6, flexShrink: 0 },
  journeyText: { fontSize: 13, color: "#cbd5e1" },
  journeyWhen: { fontSize: 11.5, color: "#64748b", marginTop: 2 },
  input: {
    width: "100%", borderRadius: 10, backgroundColor: "#0d1424", borderWidth: 1, borderColor: "rgba(255,255,255,0.1)",
    color: "#e2e8f0", fontSize: 13.5, padding: 10,
  },
  postButton: { marginTop: 8, height: 42, borderRadius: 10, backgroundColor: "#6657F2", alignItems: "center", justifyContent: "center" },
  postButtonText: { color: "#fff", fontSize: 13.5, fontWeight: "700" },
  bottomBar: {
    position: "absolute", left: 0, right: 0, bottom: 0, flexDirection: "row", gap: 8,
    padding: 16, paddingBottom: 24, backgroundColor: "rgba(11,15,26,0.96)",
    borderTopWidth: 1, borderColor: "rgba(255,255,255,0.08)",
  },
  overflowButton: {
    width: 44, height: 44, borderRadius: 12, backgroundColor: "#111827",
    borderWidth: 1, borderColor: "rgba(255,255,255,0.08)", alignItems: "center", justifyContent: "center",
  },
  primaryButton: { flex: 1, height: 46, borderRadius: 12, alignItems: "center", justifyContent: "center" },
  primaryButtonText: { color: "#fff", fontSize: 14, fontWeight: "700" },
  modalTitle: { fontSize: 17, fontWeight: "800", color: "#f1f5f9", marginBottom: 14 },
  overflowItem: { flexDirection: "row", alignItems: "center", paddingVertical: 13, borderTopWidth: 1, borderColor: "rgba(255,255,255,0.06)" },
  overflowItemText: { fontSize: 14.5, fontWeight: "600", color: "#e2e8f0" },
  label: { fontSize: 13, fontWeight: "600", color: "#94a3b8", marginBottom: 6, marginTop: 10 },
  chipRow: { flexDirection: "row", gap: 7, flexWrap: "wrap" },
  priorityChip: { paddingHorizontal: 10, paddingVertical: 7, borderRadius: 8, backgroundColor: "#0d1424", borderWidth: 1, borderColor: "rgba(255,255,255,0.1)" },
  priorityChipText: { fontSize: 12, fontWeight: "600", color: "#94a3b8" },
  modalActions: { flexDirection: "row", gap: 10, marginTop: 16 },
  modalCancel: { flex: 1, height: 48, borderRadius: 12, borderWidth: 1, borderColor: "rgba(255,255,255,0.12)", alignItems: "center", justifyContent: "center" },
  modalCancelText: { fontSize: 14, fontWeight: "700", color: "#cbd5e1" },
  modalSubmit: { width: "100%", height: 50, borderRadius: 12, backgroundColor: "#6657F2", alignItems: "center", justifyContent: "center" },
  modalSubmitText: { fontSize: 15, fontWeight: "700", color: "#fff" },
  dangerSubmit: { width: "100%", height: 50, borderRadius: 12, backgroundColor: "#ef4444", alignItems: "center", justifyContent: "center" },
  dangerSubmitFlex: { flex: 1, height: 48, borderRadius: 12, backgroundColor: "#ef4444", alignItems: "center", justifyContent: "center" },
  sheetCloseLink: { alignItems: "center", marginTop: 14 },
});
