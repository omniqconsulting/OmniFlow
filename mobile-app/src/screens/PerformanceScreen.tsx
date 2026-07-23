import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Alert, ScrollView, StyleSheet, Text, TouchableOpacity, View } from "react-native";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";

import type { AuthStackParamList } from "../navigation/AuthNavigator";
import { ApiError } from "../api/client";
import { performanceApi, type PerfComponent } from "../api/setup";
import { useTheme, type ThemeColors } from "../theme/ThemeContext";

type Props = NativeStackScreenProps<AuthStackParamList, "SetupPerformance">;

const STEP = 5;

export default function PerformanceScreen({ navigation }: Props) {
  const { colors } = useTheme();
  const styles = makeStyles(colors);
  const [components, setComponents] = useState<PerfComponent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const formula = await performanceApi.get();
      setComponents(formula.components);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Couldn't load the performance formula.");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const adjust = (key: string, delta: number) => {
    setComponents((prev) => (prev ? prev.map((c) => (c.key === key ? { ...c, weight: Math.max(0, Math.min(100, c.weight + delta)) } : c)) : prev));
  };

  const total = components?.reduce((sum, c) => sum + c.weight, 0) ?? 0;

  const save = async () => {
    if (!components) return;
    setSaving(true);
    try {
      const weights = Object.fromEntries(components.map((c) => [c.key, c.weight]));
      const updated = await performanceApi.save(null, weights);
      setComponents(updated.components);
      Alert.alert("Saved", "Performance formula updated — employee scores recalculate on next view.");
    } catch (e) {
      Alert.alert("Couldn't save", e instanceof ApiError ? e.detail : "Something went wrong.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <View style={styles.screen}>
      <View style={styles.topBar}>
        <TouchableOpacity style={styles.backButton} onPress={() => navigation.goBack()}>
          <Text style={styles.backIcon}>‹</Text>
        </TouchableOpacity>
        <View>
          <Text style={styles.title}>Performance</Text>
          <Text style={styles.subtitle}>Weighted score formula, drives employee KPIs</Text>
        </View>
      </View>

      {!components && !error ? <ActivityIndicator color={colors.teal} style={{ marginTop: 24 }} /> : null}
      {error ? <Text style={styles.error}>{error}</Text> : null}

      {components ? (
        <ScrollView style={styles.body} contentContainerStyle={styles.bodyContent}>
          <View style={[styles.totalBanner, total !== 100 && styles.totalBannerWarn]}>
            <Text style={styles.totalText}>Total weight: {total}%</Text>
            {total !== 100 ? <Text style={styles.totalWarnText}>Weights are normalized automatically if they don't sum to 100.</Text> : null}
          </View>

          {components.map((c) => (
            <View key={c.key} style={styles.card}>
              <Text style={styles.cardLabel}>{c.label}</Text>
              <View style={styles.stepperRow}>
                <TouchableOpacity style={styles.stepperButton} onPress={() => adjust(c.key, -STEP)}>
                  <Text style={styles.stepperButtonText}>−</Text>
                </TouchableOpacity>
                <Text style={styles.weightValue}>{c.weight}%</Text>
                <TouchableOpacity style={styles.stepperButton} onPress={() => adjust(c.key, STEP)}>
                  <Text style={styles.stepperButtonText}>+</Text>
                </TouchableOpacity>
              </View>
              <View style={styles.barTrack}>
                <View style={[styles.barFill, { width: `${c.weight}%` }]} />
              </View>
            </View>
          ))}

          <TouchableOpacity style={[styles.saveButton, saving && { opacity: 0.7 }]} onPress={save} disabled={saving}>
            {saving ? <ActivityIndicator color="#fff" /> : <Text style={styles.saveButtonText}>Save Formula</Text>}
          </TouchableOpacity>
        </ScrollView>
      ) : null}
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
    body: { flex: 1 },
    bodyContent: { padding: 20, paddingBottom: 40 },
    error: { color: "#f87185", fontSize: 13, marginHorizontal: 20, marginTop: 12 },
    totalBanner: { backgroundColor: "rgba(45,212,191,0.1)", borderWidth: 1, borderColor: "rgba(45,212,191,0.25)", borderRadius: 12, padding: 12, marginBottom: 16 },
    totalBannerWarn: { backgroundColor: "rgba(234,179,8,0.1)", borderColor: "rgba(234,179,8,0.3)" },
    totalText: { fontSize: 13, fontWeight: "700", color: colors.textPrimary },
    totalWarnText: { fontSize: 11, color: colors.textSecondary, marginTop: 2 },
    card: { backgroundColor: colors.cardBg, borderWidth: 1, borderColor: colors.border, borderRadius: 14, padding: 16, marginBottom: 12 },
    cardLabel: { fontSize: 13, fontWeight: "700", color: colors.textPrimary, marginBottom: 10 },
    stepperRow: { flexDirection: "row", alignItems: "center", gap: 16, marginBottom: 10 },
    stepperButton: { width: 34, height: 34, borderRadius: 10, backgroundColor: colors.screenBg, borderWidth: 1, borderColor: colors.border, alignItems: "center", justifyContent: "center" },
    stepperButtonText: { fontSize: 18, fontWeight: "700", color: colors.textPrimary },
    weightValue: { fontSize: 16, fontWeight: "800", color: colors.teal, minWidth: 50, textAlign: "center" },
    barTrack: { height: 6, borderRadius: 99, backgroundColor: colors.border, overflow: "hidden" },
    barFill: { height: "100%", borderRadius: 99, backgroundColor: colors.indigo },
    saveButton: { height: 48, borderRadius: 12, backgroundColor: colors.indigo, alignItems: "center", justifyContent: "center", marginTop: 6 },
    saveButtonText: { fontSize: 14, fontWeight: "700", color: "#fff" },
  });
}
