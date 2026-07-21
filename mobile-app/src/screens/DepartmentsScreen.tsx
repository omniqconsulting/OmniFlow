import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Alert, Modal, ScrollView, StyleSheet, Text, TextInput, TouchableOpacity, View } from "react-native";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";

import type { AuthStackParamList } from "../navigation/AuthNavigator";
import { ApiError } from "../api/client";
import { branchesApi, departmentsApi, type Branch, type Department } from "../api/setup";
import { useTheme, type ThemeColors } from "../theme/ThemeContext";

type Props = NativeStackScreenProps<AuthStackParamList, "SetupDepartments">;

export default function DepartmentsScreen({ navigation }: Props) {
  const { colors } = useTheme();
  const styles = makeStyles(colors);
  const [items, setItems] = useState<Department[] | null>(null);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [branchId, setBranchId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const [depts, brs] = await Promise.all([departmentsApi.list(), branchesApi.list()]);
      setItems(depts);
      setBranches(brs);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Couldn't load departments.");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const openCreate = () => {
    setEditingId(null);
    setName("");
    setBranchId(null);
    setModalOpen(true);
  };

  const openEdit = (d: Department) => {
    setEditingId(d.id);
    setName(d.name);
    setBranchId(d.branch_id);
    setModalOpen(true);
  };

  const save = async () => {
    if (!name.trim()) {
      Alert.alert("Name is required");
      return;
    }
    setSaving(true);
    try {
      const body = { name, branch_id: branchId };
      if (editingId) await departmentsApi.update(editingId, body);
      else await departmentsApi.create(body);
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
    Alert.alert("Delete department", "Remove this department?", [
      { text: "Cancel", style: "cancel" },
      {
        text: "Delete", style: "destructive",
        onPress: async () => {
          try {
            await departmentsApi.remove(editingId);
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
          <Text style={styles.title}>Departments</Text>
          <Text style={styles.subtitle}>Used for employee grouping & access</Text>
        </View>
        <TouchableOpacity style={styles.addButton} onPress={openCreate}>
          <Text style={styles.addButtonText}>+</Text>
        </TouchableOpacity>
      </View>

      {!items && !error ? <ActivityIndicator color={colors.teal} style={{ marginTop: 24 }} /> : null}
      {error ? <Text style={styles.error}>{error}</Text> : null}

      <ScrollView style={styles.body} contentContainerStyle={styles.bodyContent}>
        {items && items.length === 0 ? <Text style={styles.empty}>No departments yet — tap + to add one.</Text> : null}
        {items?.map((d) => (
          <TouchableOpacity key={d.id} style={styles.row} onPress={() => openEdit(d)}>
            <View style={{ flex: 1, minWidth: 0 }}>
              <Text style={styles.rowTitle}>{d.name}</Text>
              <Text style={styles.rowSubtitle}>{d.branch_name || "All branches"}</Text>
            </View>
            <Text style={styles.rowChevron}>›</Text>
          </TouchableOpacity>
        ))}
      </ScrollView>

      <Modal visible={modalOpen} animationType="slide" transparent onRequestClose={() => setModalOpen(false)}>
        <View style={styles.modalBackdrop}>
          <View style={styles.modalSheet}>
            <View style={styles.modalHandle} />
            <ScrollView contentContainerStyle={styles.modalContent}>
              <Text style={styles.modalTitle}>{editingId ? "Edit Department" : "New Department"}</Text>

              <Text style={styles.fieldLabel}>Name</Text>
              <TextInput style={styles.input} value={name} onChangeText={setName} placeholder="e.g. Cutting" placeholderTextColor={colors.textMuted} />

              <Text style={[styles.fieldLabel, { marginTop: 14 }]}>Branch</Text>
              <View style={styles.chipRow}>
                <TouchableOpacity
                  style={[styles.chip, branchId === null ? styles.chipActive : styles.chipInactive]}
                  onPress={() => setBranchId(null)}
                >
                  <Text style={[styles.chipText, branchId === null ? styles.chipTextActive : styles.chipTextInactive]}>All branches</Text>
                </TouchableOpacity>
                {branches.map((b) => (
                  <TouchableOpacity
                    key={b.id}
                    style={[styles.chip, branchId === b.id ? styles.chipActive : styles.chipInactive]}
                    onPress={() => setBranchId(b.id)}
                  >
                    <Text style={[styles.chipText, branchId === b.id ? styles.chipTextActive : styles.chipTextInactive]}>{b.name}</Text>
                  </TouchableOpacity>
                ))}
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
          </View>
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
    modalSheet: { maxHeight: "85%", backgroundColor: colors.cardBg, borderTopLeftRadius: 24, borderTopRightRadius: 24, borderTopWidth: 1, borderColor: colors.border },
    modalHandle: { width: 36, height: 4, borderRadius: 3, backgroundColor: colors.border, alignSelf: "center", marginTop: 12, marginBottom: 6 },
    modalContent: { paddingHorizontal: 22, paddingBottom: 30, paddingTop: 10 },
    modalTitle: { fontSize: 17, fontWeight: "800", color: colors.textPrimary, marginBottom: 16 },
    fieldLabel: { fontSize: 12, fontWeight: "600", color: colors.textSecondary, marginBottom: 6 },
    input: { backgroundColor: colors.screenBg, borderWidth: 1, borderColor: colors.border, borderRadius: 12, height: 46, paddingHorizontal: 14, fontSize: 13.5, color: colors.textPrimary },
    chipRow: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
    chip: { paddingVertical: 8, paddingHorizontal: 12, borderRadius: 8 },
    chipActive: { backgroundColor: "rgba(102,87,242,0.16)" },
    chipInactive: { backgroundColor: colors.screenBg, borderWidth: 1, borderColor: colors.border },
    chipText: { fontSize: 11.5, fontWeight: "700" },
    chipTextActive: { color: "#a99cf7" },
    chipTextInactive: { color: colors.textMuted },
    saveButton: { height: 48, borderRadius: 12, backgroundColor: colors.indigo, alignItems: "center", justifyContent: "center", marginTop: 20 },
    saveButtonText: { fontSize: 14, fontWeight: "700", color: "#fff" },
    deleteButton: { height: 44, borderRadius: 12, alignItems: "center", justifyContent: "center", marginTop: 10, borderWidth: 1, borderColor: "rgba(244,63,94,0.35)" },
    deleteButtonText: { fontSize: 13, fontWeight: "700", color: "#fb7185" },
    cancelButton: { alignItems: "center", justifyContent: "center", marginTop: 10, paddingVertical: 8 },
    cancelButtonText: { fontSize: 12.5, color: colors.textMuted },
  });
}
