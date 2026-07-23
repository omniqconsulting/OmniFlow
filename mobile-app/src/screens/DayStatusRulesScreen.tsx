import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Alert, KeyboardAvoidingView, Modal, Platform, ScrollView, StyleSheet, Switch, Text, TextInput, TouchableOpacity, View } from "react-native";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";

import type { AuthStackParamList } from "../navigation/AuthNavigator";
import { ApiError } from "../api/client";
import { dayStatusRulesApi, type DayStatusRule, type FieldCatalogEntry, type RuleCondition } from "../api/setup";
import { useTheme, type ThemeColors } from "../theme/ThemeContext";

type Props = NativeStackScreenProps<AuthStackParamList, "SetupDayStatusRules">;

const OUTCOMES = ["PRESENT", "HALF_DAY", "ABSENT"];

export default function DayStatusRulesScreen({ navigation }: Props) {
  const { colors } = useTheme();
  const styles = makeStyles(colors);
  const [rules, setRules] = useState<DayStatusRule[] | null>(null);
  const [fieldCatalog, setFieldCatalog] = useState<FieldCatalogEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [outcome, setOutcome] = useState("PRESENT");
  const [logic, setLogic] = useState<"ALL" | "ANY">("ALL");
  const [priority, setPriority] = useState(0);
  const [active, setActive] = useState(true);
  const [conditions, setConditions] = useState<RuleCondition[]>([]);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const [rows, fields] = await Promise.all([dayStatusRulesApi.list(), dayStatusRulesApi.fields()]);
      setRules(rows);
      setFieldCatalog(fields);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Couldn't load day-status rules.");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const defaultCondition = (): RuleCondition => {
    const f = fieldCatalog[0];
    return { field: f?.field ?? "HOURS_WORKED", operator: f?.operators[0] ?? "LT", value: "" };
  };

  const openCreate = () => {
    setEditingId(null);
    setName("");
    setOutcome("PRESENT");
    setLogic("ALL");
    setPriority((rules?.length ?? 0) + 1);
    setActive(true);
    setConditions([defaultCondition()]);
    setModalOpen(true);
  };

  const openEdit = (r: DayStatusRule) => {
    setEditingId(r.id);
    setName(r.name);
    setOutcome(r.outcome);
    setLogic(r.condition_logic);
    setPriority(r.priority);
    setActive(r.is_active);
    setConditions(r.conditions.length ? r.conditions : [defaultCondition()]);
    setModalOpen(true);
  };

  const updateCondition = (idx: number, patch: Partial<RuleCondition>) => {
    setConditions((prev) => prev.map((c, i) => (i === idx ? { ...c, ...patch } : c)));
  };

  const save = async () => {
    if (!name.trim()) {
      Alert.alert("Name is required");
      return;
    }
    if (conditions.some((c) => !c.value.trim())) {
      Alert.alert("Every condition needs a value");
      return;
    }
    setSaving(true);
    try {
      const body = { name, is_active: active, priority, condition_logic: logic, outcome, conditions };
      if (editingId) await dayStatusRulesApi.update(editingId, body);
      else await dayStatusRulesApi.create(body);
      setModalOpen(false);
      await load();
    } catch (e) {
      Alert.alert("Couldn't save", e instanceof ApiError ? e.detail : "Something went wrong.");
    } finally {
      setSaving(false);
    }
  };

  const remove = () => {
    if (!editingId) return;
    Alert.alert("Delete rule", "Remove this day-status rule?", [
      { text: "Cancel", style: "cancel" },
      {
        text: "Delete", style: "destructive",
        onPress: async () => {
          try {
            await dayStatusRulesApi.remove(editingId);
            setModalOpen(false);
            await load();
          } catch (e) {
            Alert.alert("Couldn't delete", e instanceof ApiError ? e.detail : "Something went wrong.");
          }
        },
      },
    ]);
  };

  const fieldsFor = (field: string) => fieldCatalog.find((f) => f.field === field);

  return (
    <View style={styles.screen}>
      <View style={styles.topBar}>
        <TouchableOpacity style={styles.backButton} onPress={() => navigation.goBack()}>
          <Text style={styles.backIcon}>‹</Text>
        </TouchableOpacity>
        <View style={{ flex: 1 }}>
          <Text style={styles.title}>Day-Status Rules</Text>
          <Text style={styles.subtitle}>Drives Present/Half-Day/Absent on the calendar</Text>
        </View>
        <TouchableOpacity style={styles.addButton} onPress={openCreate}>
          <Text style={styles.addButtonText}>+</Text>
        </TouchableOpacity>
      </View>

      {!rules && !error ? <ActivityIndicator color={colors.teal} style={{ marginTop: 24 }} /> : null}
      {error ? <Text style={styles.error}>{error}</Text> : null}

      <ScrollView style={styles.body} contentContainerStyle={styles.bodyContent}>
        {rules && rules.length === 0 ? <Text style={styles.empty}>No rules yet — default logic applies. Tap + to add one.</Text> : null}
        {rules?.map((r) => (
          <TouchableOpacity key={r.id} style={styles.row} onPress={() => openEdit(r)}>
            <View style={{ flex: 1, minWidth: 0 }}>
              <Text style={styles.rowTitle}>{r.name}</Text>
              <Text style={styles.rowSubtitle}>
                Priority {r.priority} · {r.outcome.replace("_", " ")} · {r.is_active ? "Active" : "Inactive"}
              </Text>
            </View>
            <Text style={styles.rowChevron}>›</Text>
          </TouchableOpacity>
        ))}
      </ScrollView>

      <Modal visible={modalOpen} animationType="slide" transparent onRequestClose={() => setModalOpen(false)}>
        <View style={styles.modalBackdrop}>
          <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : "height"} style={styles.modalSheet}>
            <View style={styles.modalHandle} />
            <ScrollView contentContainerStyle={styles.modalContent}>
              <Text style={styles.modalTitle}>{editingId ? "Edit Rule" : "New Rule"}</Text>

              <Text style={styles.fieldLabel}>Name</Text>
              <TextInput style={styles.input} value={name} onChangeText={setName} placeholder="e.g. Late check-in = Half Day" placeholderTextColor={colors.textMuted} />

              <Text style={[styles.fieldLabel, { marginTop: 14 }]}>Outcome</Text>
              <View style={styles.chipRow}>
                {OUTCOMES.map((o) => (
                  <TouchableOpacity key={o} style={[styles.chip, outcome === o ? styles.chipActive : styles.chipInactive]} onPress={() => setOutcome(o)}>
                    <Text style={[styles.chipText, outcome === o ? styles.chipTextActive : styles.chipTextInactive]}>{o.replace("_", " ")}</Text>
                  </TouchableOpacity>
                ))}
              </View>

              <Text style={[styles.fieldLabel, { marginTop: 14 }]}>Match</Text>
              <View style={styles.chipRow}>
                {(["ALL", "ANY"] as const).map((l) => (
                  <TouchableOpacity key={l} style={[styles.chip, logic === l ? styles.chipActive : styles.chipInactive]} onPress={() => setLogic(l)}>
                    <Text style={[styles.chipText, logic === l ? styles.chipTextActive : styles.chipTextInactive]}>{l === "ALL" ? "All conditions" : "Any condition"}</Text>
                  </TouchableOpacity>
                ))}
              </View>

              <View style={[styles.switchRow, { marginTop: 14 }]}>
                <Text style={styles.fieldLabel}>Active</Text>
                <Switch value={active} onValueChange={setActive} trackColor={{ false: colors.border, true: colors.teal }} thumbColor="#fff" />
              </View>

              <Text style={[styles.fieldLabel, { marginTop: 14, marginBottom: 10 }]}>Conditions</Text>
              {conditions.map((c, idx) => {
                const kind = fieldsFor(c.field);
                return (
                  <View key={idx} style={styles.conditionCard}>
                    <View style={styles.chipRow}>
                      {fieldCatalog.map((f) => (
                        <TouchableOpacity
                          key={f.field}
                          style={[styles.chip, c.field === f.field ? styles.chipActive : styles.chipInactive]}
                          onPress={() => updateCondition(idx, { field: f.field, operator: f.operators[0] })}
                        >
                          <Text style={[styles.chipText, c.field === f.field ? styles.chipTextActive : styles.chipTextInactive]}>{f.field.replace(/_/g, " ")}</Text>
                        </TouchableOpacity>
                      ))}
                    </View>
                    <View style={[styles.chipRow, { marginTop: 8 }]}>
                      {(kind?.operators ?? []).map((op) => (
                        <TouchableOpacity key={op} style={[styles.chip, c.operator === op ? styles.chipActive : styles.chipInactive]} onPress={() => updateCondition(idx, { operator: op })}>
                          <Text style={[styles.chipText, c.operator === op ? styles.chipTextActive : styles.chipTextInactive]}>{op}</Text>
                        </TouchableOpacity>
                      ))}
                    </View>
                    {kind?.kind !== "boolean" ? (
                      <TextInput
                        style={[styles.input, { marginTop: 8 }]}
                        value={c.value}
                        onChangeText={(v) => updateCondition(idx, { value: v })}
                        placeholder={kind?.kind === "time" ? "HH:MM" : "Number"}
                        placeholderTextColor={colors.textMuted}
                      />
                    ) : (
                      <TextInput style={[styles.input, { marginTop: 8, opacity: 0.5 }]} value="true" editable={false} />
                    )}
                    <TouchableOpacity onPress={() => setConditions((prev) => prev.filter((_, i) => i !== idx))}>
                      <Text style={styles.removeConditionText}>Remove condition</Text>
                    </TouchableOpacity>
                  </View>
                );
              })}
              <TouchableOpacity style={styles.addConditionButton} onPress={() => setConditions((prev) => [...prev, defaultCondition()])}>
                <Text style={styles.addConditionButtonText}>+ Add condition</Text>
              </TouchableOpacity>

              <TouchableOpacity style={[styles.saveButton, saving && { opacity: 0.7 }]} onPress={save} disabled={saving}>
                {saving ? <ActivityIndicator color="#fff" /> : <Text style={styles.saveButtonText}>{editingId ? "Save Changes" : "Create"}</Text>}
              </TouchableOpacity>

              {editingId ? (
                <TouchableOpacity style={styles.deleteButton} onPress={remove}>
                  <Text style={styles.deleteButtonText}>Delete</Text>
                </TouchableOpacity>
              ) : null}

              <TouchableOpacity style={styles.cancelButton} onPress={() => setModalOpen(false)}>
                <Text style={styles.cancelButtonText}>Cancel</Text>
              </TouchableOpacity>
            </ScrollView>
          </KeyboardAvoidingView>
        </View>
      </Modal>
    </View>
  );
}

function makeStyles(colors: ThemeColors) {
  return StyleSheet.create({
    screen: { flex: 1, backgroundColor: colors.screenBg },
    topBar: { paddingTop: 58, paddingHorizontal: 20, paddingBottom: 10, flexDirection: "row", alignItems: "center", gap: 12 },
    backButton: { width: 34, height: 34, borderRadius: 10, backgroundColor: colors.cardBg, borderWidth: 1, borderColor: colors.border, alignItems: "center", justifyContent: "center" },
    backIcon: { fontSize: 16, color: colors.textSecondary },
    title: { fontSize: 16, fontWeight: "800", color: colors.textPrimary },
    subtitle: { fontSize: 11.5, color: colors.textMuted },
    addButton: { width: 34, height: 34, borderRadius: 10, backgroundColor: colors.indigo, alignItems: "center", justifyContent: "center" },
    addButtonText: { fontSize: 19, fontWeight: "700", color: "#fff", marginTop: -2 },
    body: { flex: 1 },
    bodyContent: { padding: 20, paddingBottom: 40 },
    error: { color: "#f87185", fontSize: 13, marginHorizontal: 20, marginTop: 12 },
    empty: { color: colors.textMuted, fontSize: 13, textAlign: "center", marginTop: 30 },
    row: { flexDirection: "row", alignItems: "center", gap: 10, backgroundColor: colors.cardBg, borderWidth: 1, borderColor: colors.border, borderRadius: 14, padding: 14, marginBottom: 10 },
    rowTitle: { fontSize: 13.5, fontWeight: "700", color: colors.textPrimary },
    rowSubtitle: { fontSize: 11.5, color: colors.textSecondary, marginTop: 2 },
    rowChevron: { fontSize: 16, color: colors.textMuted },
    modalBackdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.5)", justifyContent: "flex-end" },
    modalSheet: { maxHeight: "90%", backgroundColor: colors.cardBg, borderTopLeftRadius: 24, borderTopRightRadius: 24, borderTopWidth: 1, borderColor: colors.border },
    modalHandle: { width: 36, height: 4, borderRadius: 3, backgroundColor: colors.border, alignSelf: "center", marginTop: 12, marginBottom: 6 },
    modalContent: { paddingHorizontal: 22, paddingBottom: 30, paddingTop: 10 },
    modalTitle: { fontSize: 17, fontWeight: "800", color: colors.textPrimary, marginBottom: 16 },
    fieldLabel: { fontSize: 12, fontWeight: "600", color: colors.textSecondary, marginBottom: 6 },
    input: { backgroundColor: colors.screenBg, borderWidth: 1, borderColor: colors.border, borderRadius: 12, height: 46, paddingHorizontal: 14, fontSize: 13.5, color: colors.textPrimary },
    chipRow: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
    chip: { paddingVertical: 8, paddingHorizontal: 12, borderRadius: 8 },
    chipActive: { backgroundColor: "rgba(102,87,242,0.16)" },
    chipInactive: { backgroundColor: colors.screenBg, borderWidth: 1, borderColor: colors.border },
    chipText: { fontSize: 11, fontWeight: "700" },
    chipTextActive: { color: "#a99cf7" },
    chipTextInactive: { color: colors.textMuted },
    switchRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
    conditionCard: { backgroundColor: colors.screenBg, borderWidth: 1, borderColor: colors.border, borderRadius: 12, padding: 12, marginBottom: 10 },
    removeConditionText: { fontSize: 11.5, fontWeight: "700", color: "#fb7185", marginTop: 8 },
    addConditionButton: { alignItems: "center", paddingVertical: 10 },
    addConditionButtonText: { fontSize: 12.5, fontWeight: "700", color: colors.teal },
    saveButton: { height: 48, borderRadius: 12, backgroundColor: colors.indigo, alignItems: "center", justifyContent: "center", marginTop: 10 },
    saveButtonText: { fontSize: 14, fontWeight: "700", color: "#fff" },
    deleteButton: { height: 44, borderRadius: 12, alignItems: "center", justifyContent: "center", marginTop: 10, borderWidth: 1, borderColor: "rgba(244,63,94,0.35)" },
    deleteButtonText: { fontSize: 13, fontWeight: "700", color: "#fb7185" },
    cancelButton: { alignItems: "center", justifyContent: "center", marginTop: 10, paddingVertical: 8 },
    cancelButtonText: { fontSize: 12.5, color: colors.textMuted },
  });
}
