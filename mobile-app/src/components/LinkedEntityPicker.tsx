import { useMemo, useState } from "react";
import { ScrollView, StyleSheet, Text, TextInput, TouchableOpacity, View } from "react-native";

import BottomSheet from "./BottomSheet";
import type { LinkedEntityOption } from "../api/tickets";

const TEAL = "#2DD4BF";

export type LinkedEntitySelection = {
  entityType: string;
  entityId?: string;
  entityLabel: string;
  customText?: string;
};

type Props = {
  visible: boolean;
  options: LinkedEntityOption[];
  onSelect: (selection: LinkedEntitySelection) => void;
  onClose: () => void;
};

export default function LinkedEntityPicker({ visible, options, onSelect, onClose }: Props) {
  const [groupKey, setGroupKey] = useState<string | null>(options[0]?.key ?? null);
  const [search, setSearch] = useState("");
  const [otherText, setOtherText] = useState("");

  const activeGroup = options.find((o) => o.key === groupKey) ?? null;
  const filtered = useMemo(() => {
    if (!activeGroup) return [];
    const term = search.trim().toLowerCase();
    return activeGroup.items.filter((it) => !term || it.label.toLowerCase().includes(term));
  }, [activeGroup, search]);

  return (
    <BottomSheet visible={visible} onClose={onClose}>
      <Text style={styles.title}>Link to a Setup record</Text>
      <Text style={styles.subtitle}>Optional — link customers, vendors, materials & more.</Text>

      {options.length === 0 ? (
        <Text style={styles.empty}>No entities configured yet. Add them in Setup.</Text>
      ) : (
        <>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 6, marginBottom: 10 }}>
            {options.map((o) => (
              <TouchableOpacity
                key={o.key}
                style={[styles.groupChip, groupKey === o.key && styles.groupChipActive]}
                onPress={() => { setGroupKey(o.key); setSearch(""); }}
              >
                <Text style={[styles.groupChipText, groupKey === o.key && styles.groupChipTextActive]}>{o.label}</Text>
              </TouchableOpacity>
            ))}
          </ScrollView>

          <TextInput
            style={styles.search}
            placeholder="Search…"
            placeholderTextColor="#64748b"
            value={search}
            onChangeText={setSearch}
          />

          <View style={styles.list}>
            {filtered.length === 0 ? (
              <Text style={styles.empty}>No entries in this list.</Text>
            ) : (
              filtered.map((it) => (
                <TouchableOpacity
                  key={it.id}
                  style={styles.row}
                  onPress={() =>
                    onSelect({
                      entityType: activeGroup!.key.startsWith("CUSTOM_LIST") ? "CUSTOM_LIST" : activeGroup!.key,
                      entityId: it.id,
                      entityLabel: it.label,
                    })
                  }
                >
                  <Text style={styles.rowText}>{it.label}</Text>
                  {it.detail ? <Text style={styles.rowDetail}>{it.detail}</Text> : null}
                </TouchableOpacity>
              ))
            )}
          </View>
        </>
      )}

      <Text style={styles.label}>Additional reference (free text)</Text>
      <View style={styles.otherRow}>
        <TextInput
          style={[styles.search, { flex: 1, marginBottom: 0 }]}
          placeholder="Any other reference…"
          placeholderTextColor="#64748b"
          value={otherText}
          onChangeText={setOtherText}
        />
        <TouchableOpacity
          style={styles.addOtherButton}
          onPress={() => {
            if (!otherText.trim()) return;
            onSelect({ entityType: "OTHER", entityLabel: otherText.trim(), customText: otherText.trim() });
            setOtherText("");
          }}
        >
          <Text style={styles.addOtherButtonText}>Add</Text>
        </TouchableOpacity>
      </View>
    </BottomSheet>
  );
}

const styles = StyleSheet.create({
  title: { fontSize: 16, fontWeight: "800", color: "#f1f5f9" },
  subtitle: { fontSize: 11.5, color: "#94a3b8", marginTop: 3, marginBottom: 14 },
  empty: { fontSize: 12.5, color: "#64748b", textAlign: "center", paddingVertical: 16 },
  groupChip: { paddingHorizontal: 12, paddingVertical: 7, borderRadius: 999, backgroundColor: "#0d1424", borderWidth: 1, borderColor: "rgba(255,255,255,0.1)" },
  groupChipActive: { backgroundColor: "rgba(102,87,242,0.16)", borderColor: "rgba(102,87,242,0.4)" },
  groupChipText: { fontSize: 11.5, fontWeight: "600", color: "#94a3b8" },
  groupChipTextActive: { color: "#a99cf7" },
  search: {
    height: 42, borderRadius: 10, backgroundColor: "#0d1424", borderWidth: 1, borderColor: "rgba(255,255,255,0.1)",
    color: "#e2e8f0", fontSize: 13, paddingHorizontal: 12, marginBottom: 8,
  },
  list: { maxHeight: 220 },
  row: { paddingVertical: 10, paddingHorizontal: 6, borderTopWidth: 1, borderColor: "rgba(255,255,255,0.06)" },
  rowText: { fontSize: 13, fontWeight: "600", color: "#e2e8f0" },
  rowDetail: { fontSize: 11, color: "#64748b", marginTop: 1 },
  label: { fontSize: 11.5, fontWeight: "600", color: "#94a3b8", marginTop: 16, marginBottom: 6 },
  otherRow: { flexDirection: "row", gap: 8 },
  addOtherButton: { paddingHorizontal: 16, height: 42, borderRadius: 10, backgroundColor: TEAL, alignItems: "center", justifyContent: "center" },
  addOtherButtonText: { fontSize: 12.5, fontWeight: "700", color: "#0b0f1a" },
});
