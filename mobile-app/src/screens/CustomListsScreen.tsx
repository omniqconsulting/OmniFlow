import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Alert, Modal, ScrollView, StyleSheet, Text, TextInput, TouchableOpacity, View } from "react-native";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";

import type { AuthStackParamList } from "../navigation/AuthNavigator";
import { ApiError } from "../api/client";
import { listsApi, type RefList } from "../api/setup";
import { useTheme, type ThemeColors } from "../theme/ThemeContext";

type Props = NativeStackScreenProps<AuthStackParamList, "SetupLists">;

export default function CustomListsScreen({ navigation }: Props) {
  const { colors } = useTheme();
  const styles = makeStyles(colors);
  const [lists, setLists] = useState<RefList[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openListId, setOpenListId] = useState<string | null>(null);
  const [newListName, setNewListName] = useState("");
  const [newListModalOpen, setNewListModalOpen] = useState(false);
  const [newItemValue, setNewItemValue] = useState("");
  const [editingItem, setEditingItem] = useState<{ id: string; value: string } | null>(null);

  const load = useCallback(async () => {
    try {
      const rows = await listsApi.list();
      setLists(rows);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Couldn't load custom lists.");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const openList = lists?.find((l) => l.id === openListId) ?? null;

  const createList = async () => {
    if (!newListName.trim()) return;
    try {
      await listsApi.create(newListName.trim());
      setNewListName("");
      setNewListModalOpen(false);
      await load();
    } catch (e) {
      Alert.alert("Couldn't create list", e instanceof ApiError ? e.detail : "Something went wrong.");
    }
  };

  const deleteList = (listId: string) => {
    Alert.alert("Delete list", "This removes the list and all its values.", [
      { text: "Cancel", style: "cancel" },
      {
        text: "Delete", style: "destructive",
        onPress: async () => {
          try {
            await listsApi.remove(listId);
            setOpenListId(null);
            await load();
          } catch (e) {
            Alert.alert("Couldn't delete", e instanceof ApiError ? e.detail : "Something went wrong.");
          }
        },
      },
    ]);
  };

  const addItem = async () => {
    if (!openListId || !newItemValue.trim()) return;
    try {
      await listsApi.addItem(openListId, newItemValue.trim());
      setNewItemValue("");
      await load();
    } catch (e) {
      Alert.alert("Couldn't add value", e instanceof ApiError ? e.detail : "Something went wrong.");
    }
  };

  const saveItem = async () => {
    if (!openListId || !editingItem || !editingItem.value.trim()) return;
    try {
      await listsApi.updateItem(openListId, editingItem.id, editingItem.value.trim());
      setEditingItem(null);
      await load();
    } catch (e) {
      Alert.alert("Couldn't save value", e instanceof ApiError ? e.detail : "Something went wrong.");
    }
  };

  const removeItem = (itemId: string) => {
    if (!openListId) return;
    Alert.alert("Remove value", "Remove this value from the list?", [
      { text: "Cancel", style: "cancel" },
      {
        text: "Remove", style: "destructive",
        onPress: async () => {
          try {
            await listsApi.removeItem(openListId, itemId);
            await load();
          } catch (e) {
            Alert.alert("Couldn't remove", e instanceof ApiError ? e.detail : "Something went wrong.");
          }
        },
      },
    ]);
  };

  if (openList) {
    return (
      <View style={styles.screen}>
        <View style={styles.topBar}>
          <TouchableOpacity style={styles.backButton} onPress={() => setOpenListId(null)}>
            <Text style={styles.backIcon}>‹</Text>
          </TouchableOpacity>
          <View style={{ flex: 1 }}>
            <Text style={styles.title}>{openList.list_name}</Text>
            <Text style={styles.subtitle}>{openList.items.length} values</Text>
          </View>
          <TouchableOpacity style={styles.deleteListButton} onPress={() => deleteList(openList.id)}>
            <Text style={styles.deleteListButtonText}>🗑</Text>
          </TouchableOpacity>
        </View>

        <ScrollView style={styles.body} contentContainerStyle={styles.bodyContent}>
          <View style={styles.addItemRow}>
            <TextInput
              style={[styles.input, { flex: 1 }]}
              value={newItemValue}
              onChangeText={setNewItemValue}
              placeholder="Add a value…"
              placeholderTextColor={colors.textMuted}
            />
            <TouchableOpacity style={styles.addItemButton} onPress={addItem}>
              <Text style={styles.addItemButtonText}>Add</Text>
            </TouchableOpacity>
          </View>

          {openList.items.map((item) =>
            editingItem?.id === item.id ? (
              <View key={item.id} style={styles.addItemRow}>
                <TextInput
                  style={[styles.input, { flex: 1 }]}
                  value={editingItem.value}
                  onChangeText={(v) => setEditingItem({ id: item.id, value: v })}
                  autoFocus
                />
                <TouchableOpacity style={styles.addItemButton} onPress={saveItem}>
                  <Text style={styles.addItemButtonText}>Save</Text>
                </TouchableOpacity>
              </View>
            ) : (
              <View key={item.id} style={styles.itemRow}>
                <Text style={styles.itemText}>{item.value}</Text>
                <View style={{ flexDirection: "row", gap: 14 }}>
                  <TouchableOpacity onPress={() => setEditingItem({ id: item.id, value: item.value })}>
                    <Text style={styles.itemAction}>Edit</Text>
                  </TouchableOpacity>
                  <TouchableOpacity onPress={() => removeItem(item.id)}>
                    <Text style={[styles.itemAction, { color: "#fb7185" }]}>Remove</Text>
                  </TouchableOpacity>
                </View>
              </View>
            )
          )}
          {openList.items.length === 0 ? <Text style={styles.empty}>No values yet — add one above.</Text> : null}
        </ScrollView>
      </View>
    );
  }

  return (
    <View style={styles.screen}>
      <View style={styles.topBar}>
        <TouchableOpacity style={styles.backButton} onPress={() => navigation.goBack()}>
          <Text style={styles.backIcon}>‹</Text>
        </TouchableOpacity>
        <View style={{ flex: 1 }}>
          <Text style={styles.title}>Custom Lists</Text>
          <Text style={styles.subtitle}>Reason codes, machines & other dropdowns</Text>
        </View>
        <TouchableOpacity style={styles.addButton} onPress={() => setNewListModalOpen(true)}>
          <Text style={styles.addButtonText}>+</Text>
        </TouchableOpacity>
      </View>

      {!lists && !error ? <ActivityIndicator color={colors.teal} style={{ marginTop: 24 }} /> : null}
      {error ? <Text style={styles.error}>{error}</Text> : null}

      <ScrollView style={styles.body} contentContainerStyle={styles.bodyContent}>
        {lists && lists.length === 0 ? <Text style={styles.empty}>No custom lists yet — tap + to add one.</Text> : null}
        {lists?.map((l) => (
          <TouchableOpacity key={l.id} style={styles.row} onPress={() => setOpenListId(l.id)}>
            <View style={{ flex: 1, minWidth: 0 }}>
              <Text style={styles.rowTitle}>{l.list_name}</Text>
              <Text style={styles.rowSubtitle}>{l.items.length} values</Text>
            </View>
            <Text style={styles.rowChevron}>›</Text>
          </TouchableOpacity>
        ))}
      </ScrollView>

      <Modal visible={newListModalOpen} animationType="slide" transparent onRequestClose={() => setNewListModalOpen(false)}>
        <View style={styles.modalBackdrop}>
          <View style={styles.modalSheet}>
            <View style={styles.modalHandle} />
            <View style={styles.modalContent}>
              <Text style={styles.modalTitle}>New Custom List</Text>
              <Text style={styles.fieldLabel}>List name</Text>
              <TextInput style={styles.input} value={newListName} onChangeText={setNewListName} placeholder="e.g. Delay Reasons" placeholderTextColor={colors.textMuted} />
              <TouchableOpacity style={styles.saveButton} onPress={createList}>
                <Text style={styles.saveButtonText}>Create</Text>
              </TouchableOpacity>
              <TouchableOpacity style={styles.cancelButton} onPress={() => setNewListModalOpen(false)}>
                <Text style={styles.cancelButtonText}>Cancel</Text>
              </TouchableOpacity>
            </View>
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
    deleteListButton: { width: 34, height: 34, borderRadius: 10, backgroundColor: "rgba(244,63,94,0.14)", alignItems: "center", justifyContent: "center" },
    deleteListButtonText: { fontSize: 15 },
    body: { flex: 1 },
    bodyContent: { padding: 20, paddingBottom: 40 },
    error: { color: "#f87185", fontSize: 13, marginHorizontal: 20, marginTop: 12 },
    empty: { color: colors.textMuted, fontSize: 13, textAlign: "center", marginTop: 30 },
    row: { flexDirection: "row", alignItems: "center", gap: 10, backgroundColor: colors.cardBg, borderWidth: 1, borderColor: colors.border, borderRadius: 14, padding: 14, marginBottom: 10 },
    rowTitle: { fontSize: 13.5, fontWeight: "700", color: colors.textPrimary },
    rowSubtitle: { fontSize: 11.5, color: colors.textSecondary, marginTop: 2 },
    rowChevron: { fontSize: 16, color: colors.textMuted },
    addItemRow: { flexDirection: "row", gap: 8, marginBottom: 10 },
    addItemButton: { height: 46, paddingHorizontal: 16, borderRadius: 12, backgroundColor: colors.indigo, alignItems: "center", justifyContent: "center" },
    addItemButtonText: { fontSize: 12.5, fontWeight: "700", color: "#fff" },
    itemRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", backgroundColor: colors.cardBg, borderWidth: 1, borderColor: colors.border, borderRadius: 12, padding: 12, marginBottom: 8 },
    itemText: { fontSize: 13, color: colors.textPrimary, flex: 1 },
    itemAction: { fontSize: 12, fontWeight: "700", color: colors.teal },
    input: { backgroundColor: colors.screenBg, borderWidth: 1, borderColor: colors.border, borderRadius: 12, height: 46, paddingHorizontal: 14, fontSize: 13.5, color: colors.textPrimary },
    modalBackdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.5)", justifyContent: "flex-end" },
    modalSheet: { backgroundColor: colors.cardBg, borderTopLeftRadius: 24, borderTopRightRadius: 24, borderTopWidth: 1, borderColor: colors.border },
    modalHandle: { width: 36, height: 4, borderRadius: 3, backgroundColor: colors.border, alignSelf: "center", marginTop: 12, marginBottom: 6 },
    modalContent: { paddingHorizontal: 22, paddingBottom: 30, paddingTop: 10 },
    modalTitle: { fontSize: 17, fontWeight: "800", color: colors.textPrimary, marginBottom: 16 },
    fieldLabel: { fontSize: 12, fontWeight: "600", color: colors.textSecondary, marginBottom: 6 },
    saveButton: { height: 48, borderRadius: 12, backgroundColor: colors.indigo, alignItems: "center", justifyContent: "center", marginTop: 16 },
    saveButtonText: { fontSize: 14, fontWeight: "700", color: "#fff" },
    cancelButton: { alignItems: "center", justifyContent: "center", marginTop: 10, paddingVertical: 8 },
    cancelButtonText: { fontSize: 12.5, color: colors.textMuted },
  });
}
