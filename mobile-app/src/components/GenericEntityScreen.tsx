import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Modal,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";

import { ApiError } from "../api/client";
import { useTheme, type ThemeColors } from "../theme/ThemeContext";

export type FieldDef =
  | { key: string; label: string; type: "text"; multiline?: boolean; keyboardType?: "default" | "numeric" | "email-address" | "phone-pad" }
  | { key: string; label: string; type: "switch" };

export type EntityApi<T> = {
  list: () => Promise<T[]>;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  create: (body: any) => Promise<T>;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  update: (id: string, body: any) => Promise<T>;
  remove: (id: string) => Promise<void>;
};

type Props<T extends { id: string }> = {
  title: string;
  subtitle: string;
  fields: FieldDef[];
  defaultValues: Record<string, unknown>;
  api: EntityApi<T>;
  rowTitle: (item: T) => string;
  rowSubtitle: (item: T) => string;
  toFormValues: (item: T) => Record<string, unknown>;
  onBack: () => void;
};

export default function GenericEntityScreen<T extends { id: string }>({
  title,
  subtitle,
  fields,
  defaultValues,
  api,
  rowTitle,
  rowSubtitle,
  toFormValues,
  onBack,
}: Props<T>) {
  const { colors } = useTheme();
  const styles = makeStyles(colors);
  const [items, setItems] = useState<T[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [values, setValues] = useState<Record<string, unknown>>(defaultValues);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const rows = await api.list();
      setItems(rows);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Couldn't load.");
    }
  }, [api]);

  useEffect(() => {
    load();
  }, [load]);

  const openCreate = () => {
    setEditingId(null);
    setValues(defaultValues);
    setModalOpen(true);
  };

  const openEdit = (item: T) => {
    setEditingId(item.id);
    setValues(toFormValues(item));
    setModalOpen(true);
  };

  const save = async () => {
    setSaving(true);
    try {
      if (editingId) {
        await api.update(editingId, values);
      } else {
        await api.create(values);
      }
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
    Alert.alert("Delete", "Remove this entry?", [
      { text: "Cancel", style: "cancel" },
      {
        text: "Delete",
        style: "destructive",
        onPress: async () => {
          try {
            await api.remove(editingId);
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
        <TouchableOpacity style={styles.backButton} onPress={onBack}>
          <Text style={styles.backIcon}>‹</Text>
        </TouchableOpacity>
        <View style={{ flex: 1 }}>
          <Text style={styles.title}>{title}</Text>
          <Text style={styles.subtitle}>{subtitle}</Text>
        </View>
        <TouchableOpacity style={styles.addButton} onPress={openCreate}>
          <Text style={styles.addButtonText}>+</Text>
        </TouchableOpacity>
      </View>

      {!items && !error ? <ActivityIndicator color={colors.teal} style={{ marginTop: 24 }} /> : null}
      {error ? <Text style={styles.error}>{error}</Text> : null}

      <ScrollView style={styles.body} contentContainerStyle={styles.bodyContent}>
        {items && items.length === 0 ? <Text style={styles.empty}>Nothing here yet — tap + to add one.</Text> : null}
        {items?.map((item) => (
          <TouchableOpacity key={item.id} style={styles.row} onPress={() => openEdit(item)}>
            <View style={{ flex: 1, minWidth: 0 }}>
              <Text style={styles.rowTitle}>{rowTitle(item)}</Text>
              <Text style={styles.rowSubtitle}>{rowSubtitle(item)}</Text>
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
              <Text style={styles.modalTitle}>{editingId ? `Edit ${title}` : `New ${title}`}</Text>
              {fields.map((f) => (
                <View key={f.key} style={styles.field}>
                  {f.type === "switch" ? (
                    <View style={styles.switchRow}>
                      <Text style={styles.fieldLabel}>{f.label}</Text>
                      <Switch
                        value={Boolean(values[f.key])}
                        onValueChange={(v) => setValues((prev) => ({ ...prev, [f.key]: v }))}
                        trackColor={{ false: colors.border, true: colors.teal }}
                        thumbColor="#ffffff"
                      />
                    </View>
                  ) : (
                    <>
                      <Text style={styles.fieldLabel}>{f.label}</Text>
                      <TextInput
                        style={[styles.input, f.multiline && styles.inputMultiline]}
                        value={String(values[f.key] ?? "")}
                        onChangeText={(v) => setValues((prev) => ({ ...prev, [f.key]: v }))}
                        placeholder={f.label}
                        placeholderTextColor={colors.textMuted}
                        multiline={f.multiline}
                        keyboardType={f.keyboardType ?? "default"}
                      />
                    </>
                  )}
                </View>
              ))}

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
    backButton: {
      width: 34, height: 34, borderRadius: 10, backgroundColor: colors.cardBg, borderWidth: 1, borderColor: colors.border,
      alignItems: "center", justifyContent: "center",
    },
    backIcon: { fontSize: 16, color: colors.textSecondary },
    title: { fontSize: 16, fontWeight: "800", color: colors.textPrimary },
    subtitle: { fontSize: 11.5, color: colors.textMuted },
    addButton: {
      width: 34, height: 34, borderRadius: 10, backgroundColor: colors.indigo,
      alignItems: "center", justifyContent: "center",
    },
    addButtonText: { fontSize: 19, fontWeight: "700", color: "#fff", marginTop: -2 },
    body: { flex: 1 },
    bodyContent: { padding: 20, paddingBottom: 40 },
    error: { color: "#f87185", fontSize: 13, marginHorizontal: 20, marginTop: 12 },
    empty: { color: colors.textMuted, fontSize: 13, textAlign: "center", marginTop: 30 },
    row: {
      flexDirection: "row", alignItems: "center", gap: 10, backgroundColor: colors.cardBg, borderWidth: 1,
      borderColor: colors.border, borderRadius: 14, padding: 14, marginBottom: 10,
    },
    rowTitle: { fontSize: 13.5, fontWeight: "700", color: colors.textPrimary },
    rowSubtitle: { fontSize: 11.5, color: colors.textSecondary, marginTop: 2 },
    rowChevron: { fontSize: 16, color: colors.textMuted },
    modalBackdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.5)", justifyContent: "flex-end" },
    modalSheet: { maxHeight: "85%", backgroundColor: colors.cardBg, borderTopLeftRadius: 24, borderTopRightRadius: 24, borderTopWidth: 1, borderColor: colors.border },
    modalHandle: { width: 36, height: 4, borderRadius: 3, backgroundColor: colors.border, alignSelf: "center", marginTop: 12, marginBottom: 6 },
    modalContent: { paddingHorizontal: 22, paddingBottom: 30, paddingTop: 10 },
    modalTitle: { fontSize: 17, fontWeight: "800", color: colors.textPrimary, marginBottom: 16 },
    field: { marginBottom: 14 },
    fieldLabel: { fontSize: 12, fontWeight: "600", color: colors.textSecondary, marginBottom: 6 },
    input: {
      backgroundColor: colors.screenBg, borderWidth: 1, borderColor: colors.border, borderRadius: 12,
      height: 46, paddingHorizontal: 14, fontSize: 13.5, color: colors.textPrimary,
    },
    inputMultiline: { height: 76, paddingTop: 10, textAlignVertical: "top" },
    switchRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
    saveButton: { height: 48, borderRadius: 12, backgroundColor: colors.indigo, alignItems: "center", justifyContent: "center", marginTop: 6 },
    saveButtonText: { fontSize: 14, fontWeight: "700", color: "#fff" },
    deleteButton: { height: 44, borderRadius: 12, alignItems: "center", justifyContent: "center", marginTop: 10, borderWidth: 1, borderColor: "rgba(244,63,94,0.35)" },
    deleteButtonText: { fontSize: 13, fontWeight: "700", color: "#fb7185" },
    cancelButton: { alignItems: "center", justifyContent: "center", marginTop: 10, paddingVertical: 8 },
    cancelButtonText: { fontSize: 12.5, color: colors.textMuted },
  });
}
