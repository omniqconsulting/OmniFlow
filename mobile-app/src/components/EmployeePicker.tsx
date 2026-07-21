import { useMemo, useState } from "react";
import { StyleSheet, Text, TextInput, TouchableOpacity, View } from "react-native";

import BottomSheet from "./BottomSheet";
import type { EmployeeOption } from "../api/tickets";

const TEAL = "#2DD4BF";

type SingleProps = {
  visible: boolean;
  title: string;
  employees: EmployeeOption[];
  excludeId?: string;
  multiSelect?: false;
  onSelect: (employee: EmployeeOption) => void;
  onClose: () => void;
};

type MultiProps = {
  visible: boolean;
  title: string;
  employees: EmployeeOption[];
  excludeId?: string;
  multiSelect: true;
  selectedIds: string[];
  onToggle: (employee: EmployeeOption) => void;
  onDone: () => void;
  onClose: () => void;
};

type Props = SingleProps | MultiProps;

export default function EmployeePicker(props: Props) {
  const { visible, title, employees, excludeId, onClose } = props;
  const [search, setSearch] = useState("");

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase();
    return employees
      .filter((e) => e.id !== excludeId)
      .filter((e) => !term || e.name.toLowerCase().includes(term));
  }, [employees, excludeId, search]);

  return (
    <BottomSheet visible={visible} onClose={onClose}>
      <Text style={styles.title}>{title}</Text>
      <TextInput
        style={styles.search}
        placeholder="Search employees…"
        placeholderTextColor="#64748b"
        value={search}
        onChangeText={setSearch}
        autoCorrect={false}
      />
      <View style={styles.list}>
        {filtered.length === 0 ? (
          <Text style={styles.empty}>No employees match "{search}".</Text>
        ) : (
          filtered.map((e) => {
            const checked = props.multiSelect ? props.selectedIds.includes(e.id) : false;
            return (
              <TouchableOpacity
                key={e.id}
                style={styles.row}
                onPress={() => (props.multiSelect ? props.onToggle(e) : props.onSelect(e))}
              >
                <View style={styles.avatar}>
                  <Text style={styles.avatarText}>{e.name.slice(0, 1).toUpperCase()}</Text>
                </View>
                <Text style={styles.rowText}>{e.name}</Text>
                {props.multiSelect ? (
                  <View style={[styles.checkbox, checked && styles.checkboxChecked]}>
                    {checked ? <Text style={styles.checkboxMark}>✓</Text> : null}
                  </View>
                ) : null}
              </TouchableOpacity>
            );
          })
        )}
      </View>
      {props.multiSelect ? (
        <TouchableOpacity style={styles.doneButton} onPress={props.onDone}>
          <Text style={styles.doneButtonText}>
            Done{props.selectedIds.length > 0 ? ` (${props.selectedIds.length})` : ""}
          </Text>
        </TouchableOpacity>
      ) : null}
    </BottomSheet>
  );
}

const styles = StyleSheet.create({
  title: { fontSize: 17, fontWeight: "800", color: "#f1f5f9", marginBottom: 14 },
  search: {
    height: 46, borderRadius: 12, backgroundColor: "#0d1424", borderWidth: 1, borderColor: "rgba(255,255,255,0.1)",
    color: "#e2e8f0", fontSize: 14.5, paddingHorizontal: 14, marginBottom: 10,
  },
  list: { gap: 2 },
  empty: { fontSize: 13.5, color: "#64748b", textAlign: "center", paddingVertical: 20 },
  row: {
    flexDirection: "row", alignItems: "center", gap: 12, paddingVertical: 12, paddingHorizontal: 6,
    borderTopWidth: 1, borderColor: "rgba(255,255,255,0.06)",
  },
  avatar: { width: 34, height: 34, borderRadius: 9, backgroundColor: "#1e293b", alignItems: "center", justifyContent: "center" },
  avatarText: { fontSize: 13, fontWeight: "700", color: TEAL },
  rowText: { fontSize: 14.5, fontWeight: "600", color: "#e2e8f0", flex: 1 },
  checkbox: {
    width: 20, height: 20, borderRadius: 6, borderWidth: 1.5, borderColor: "rgba(255,255,255,0.25)",
    alignItems: "center", justifyContent: "center",
  },
  checkboxChecked: { backgroundColor: TEAL, borderColor: TEAL },
  checkboxMark: { fontSize: 12, fontWeight: "800", color: "#0b0f1a" },
  doneButton: { marginTop: 14, height: 48, borderRadius: 12, backgroundColor: TEAL, alignItems: "center", justifyContent: "center" },
  doneButtonText: { fontSize: 14, fontWeight: "700", color: "#0b0f1a" },
});
