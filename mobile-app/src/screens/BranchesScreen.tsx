import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Alert, KeyboardAvoidingView, Modal, Platform, ScrollView, StyleSheet, Text, TextInput, TouchableOpacity, View } from "react-native";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";

import type { AuthStackParamList } from "../navigation/AuthNavigator";
import { ApiError } from "../api/client";
import { branchesApi, type Branch } from "../api/setup";
import { useTheme, type ThemeColors } from "../theme/ThemeContext";

type Props = NativeStackScreenProps<AuthStackParamList, "SetupBranches">;

const DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export default function BranchesScreen({ navigation, route }: Props) {
  const { user } = route.params;
  const { colors } = useTheme();
  const styles = makeStyles(colors);
  const [items, setItems] = useState<Branch[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [address, setAddress] = useState("");
  const [offDays, setOffDays] = useState<number[]>([6]);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const rows = await branchesApi.list();
      setItems(rows);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Couldn't load branches.");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const openCreate = () => {
    setEditingId(null);
    setName("");
    setAddress("");
    setOffDays([6]);
    setModalOpen(true);
  };

  const openEdit = (b: Branch) => {
    setEditingId(b.id);
    setName(b.name);
    setAddress(b.address ?? "");
    setOffDays(b.weekly_off_days);
    setModalOpen(true);
  };

  const toggleDay = (i: number) => {
    setOffDays((prev) => (prev.includes(i) ? prev.filter((d) => d !== i) : [...prev, i]));
  };

  const save = async () => {
    if (!name.trim()) {
      Alert.alert("Name is required");
      return;
    }
    setSaving(true);
    try {
      const body = { name, address, weekly_off_days: offDays };
      if (editingId) await branchesApi.update(editingId, body);
      else await branchesApi.create(body);
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
    Alert.alert("Delete branch", "Remove this branch?", [
      { text: "Cancel", style: "cancel" },
      {
        text: "Delete", style: "destructive",
        onPress: async () => {
          try {
            await branchesApi.remove(editingId);
            setModalOpen(false);
            await load();
          } catch (e) {
            Alert.alert("Couldn't delete", e instanceof ApiError ? e.detail : "Something went wrong.");
          }
        },
      },
    ]);
  };

  return (
    <View style={styles.screen}>
      <View style={styles.topBar}>
        <TouchableOpacity style={styles.backButton} onPress={() => navigation.goBack()}>
          <Text style={styles.backIcon}>‹</Text>
        </TouchableOpacity>
        <View style={{ flex: 1 }}>
          <Text style={styles.title}>Branches</Text>
          <Text style={styles.subtitle}>Weekly-off feeds the attendance calendar</Text>
        </View>
        <TouchableOpacity style={styles.addButton} onPress={openCreate}>
          <Text style={styles.addButtonText}>+</Text>
        </TouchableOpacity>
      </View>

      {!items && !error ? <ActivityIndicator color={colors.teal} style={{ marginTop: 24 }} /> : null}
      {error ? <Text style={styles.error}>{error}</Text> : null}

      <ScrollView style={styles.body} contentContainerStyle={styles.bodyContent}>
        <TouchableOpacity style={styles.departmentsLink} onPress={() => navigation.navigate("SetupDepartments", { user })}>
          <Text style={styles.departmentsLinkText}>Manage Departments →</Text>
        </TouchableOpacity>
        {items && items.length === 0 ? <Text style={styles.empty}>No branches yet — tap + to add one.</Text> : null}
        {items?.map((b) => (
          <TouchableOpacity key={b.id} style={styles.row} onPress={() => openEdit(b)}>
            <View style={{ flex: 1, minWidth: 0 }}>
              <Text style={styles.rowTitle}>{b.name}</Text>
              <Text style={styles.rowSubtitle}>
                {b.address || "No address"} · Off: {b.weekly_off_days.map((d) => DAY_LABELS[d]).join(", ") || "None"}
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
              <Text style={styles.modalTitle}>{editingId ? "Edit Branch" : "New Branch"}</Text>

              <Text style={styles.fieldLabel}>Name</Text>
              <TextInput style={styles.input} value={name} onChangeText={setName} placeholder="e.g. Main Factory" placeholderTextColor={colors.textMuted} />

              <Text style={[styles.fieldLabel, { marginTop: 14 }]}>Address</Text>
              <TextInput
                style={[styles.input, styles.inputMultiline]}
                value={address}
                onChangeText={setAddress}
                placeholder="Address"
                placeholderTextColor={colors.textMuted}
                multiline
              />

              <Text style={[styles.fieldLabel, { marginTop: 14 }]}>Weekly Off Days</Text>
              <View style={styles.dayChips}>
                {DAY_LABELS.map((label, i) => {
                  const active = offDays.includes(i);
                  return (
                    <TouchableOpacity
                      key={label}
                      style={[styles.dayChip, active ? styles.dayChipActive : styles.dayChipInactive]}
                      onPress={() => toggleDay(i)}
                    >
                      <Text style={[styles.dayChipText, active ? styles.dayChipTextActive : styles.dayChipTextInactive]}>{label}</Text>
                    </TouchableOpacity>
                  );
                })}
              </View>

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
    departmentsLink: { alignSelf: "flex-start", marginBottom: 14 },
    departmentsLinkText: { fontSize: 12.5, fontWeight: "700", color: colors.teal },
    row: { flexDirection: "row", alignItems: "center", gap: 10, backgroundColor: colors.cardBg, borderWidth: 1, borderColor: colors.border, borderRadius: 14, padding: 14, marginBottom: 10 },
    rowTitle: { fontSize: 13.5, fontWeight: "700", color: colors.textPrimary },
    rowSubtitle: { fontSize: 11.5, color: colors.textSecondary, marginTop: 2 },
    rowChevron: { fontSize: 16, color: colors.textMuted },
    modalBackdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.5)", justifyContent: "flex-end" },
    modalSheet: { maxHeight: "85%", backgroundColor: colors.cardBg, borderTopLeftRadius: 24, borderTopRightRadius: 24, borderTopWidth: 1, borderColor: colors.border },
    modalHandle: { width: 36, height: 4, borderRadius: 3, backgroundColor: colors.border, alignSelf: "center", marginTop: 12, marginBottom: 6 },
    modalContent: { paddingHorizontal: 22, paddingBottom: 30, paddingTop: 10 },
    modalTitle: { fontSize: 17, fontWeight: "800", color: colors.textPrimary, marginBottom: 16 },
    fieldLabel: { fontSize: 12, fontWeight: "600", color: colors.textSecondary, marginBottom: 6 },
    input: { backgroundColor: colors.screenBg, borderWidth: 1, borderColor: colors.border, borderRadius: 12, height: 46, paddingHorizontal: 14, fontSize: 13.5, color: colors.textPrimary },
    inputMultiline: { height: 76, paddingTop: 10, textAlignVertical: "top" },
    dayChips: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
    dayChip: { paddingVertical: 8, paddingHorizontal: 12, borderRadius: 8 },
    dayChipActive: { backgroundColor: "rgba(102,87,242,0.16)" },
    dayChipInactive: { backgroundColor: colors.screenBg, borderWidth: 1, borderColor: colors.border },
    dayChipText: { fontSize: 11, fontWeight: "700" },
    dayChipTextActive: { color: "#a99cf7" },
    dayChipTextInactive: { color: colors.textMuted },
    saveButton: { height: 48, borderRadius: 12, backgroundColor: colors.indigo, alignItems: "center", justifyContent: "center", marginTop: 20 },
    saveButtonText: { fontSize: 14, fontWeight: "700", color: "#fff" },
    deleteButton: { height: 44, borderRadius: 12, alignItems: "center", justifyContent: "center", marginTop: 10, borderWidth: 1, borderColor: "rgba(244,63,94,0.35)" },
    deleteButtonText: { fontSize: 13, fontWeight: "700", color: "#fb7185" },
    cancelButton: { alignItems: "center", justifyContent: "center", marginTop: 10, paddingVertical: 8 },
    cancelButtonText: { fontSize: 12.5, color: colors.textMuted },
  });
}
