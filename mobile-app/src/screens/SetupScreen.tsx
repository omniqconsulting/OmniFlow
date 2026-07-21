import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Alert, ScrollView, StyleSheet, Text, TouchableOpacity, View } from "react-native";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";

import type { AuthStackParamList } from "../navigation/AuthNavigator";
import { getSetupOverview, type SetupOverview } from "../api/setup";
import { ApiError } from "../api/client";
import { useTheme, type ThemeColors } from "../theme/ThemeContext";

type Props = NativeStackScreenProps<AuthStackParamList, "Setup">;

// Every row now opens a real sub-screen, each backed by the same tables the
// website's Setup pages read/write (see app/api_v1/setup_reference.py and
// setup_config.py) — so a branch/customer/employee/etc. added here shows up
// identically everywhere else in the app and on the website. "Flows" is the
// one exception: it's read + active-toggle only here, matching the design —
// the stage/routing builder stays desktop-only.
const ROW_SCREEN: Record<string, keyof import("../navigation/AuthNavigator").AuthStackParamList> = {
  notifications: "SetupNotifications",
  branches: "SetupBranches",
  employees: "SetupEmployees",
  customers: "SetupCustomers",
  products: "SetupProducts",
  vendors: "SetupVendors",
  materials: "SetupMaterials",
  lists: "SetupLists",
  uom: "SetupUom",
  flows: "SetupFlows",
  performance: "SetupPerformance",
  day_status: "SetupDayStatusRules",
};

export default function SetupScreen({ navigation, route }: Props) {
  const { user } = route.params;
  const { colors } = useTheme();
  const styles = makeStyles(colors);
  const [overview, setOverview] = useState<SetupOverview | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await getSetupOverview();
      setOverview(data);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Couldn't load Setup.");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const onRowPress = (rowKey: string, label: string) => {
    const screen = ROW_SCREEN[rowKey];
    if (screen) {
      // Every mapped screen takes the same { user } param shape — the union
      // type here is wide enough that TS can't narrow it from a dynamic key.
      (navigation as any).navigate(screen, { user });
      return;
    }
    Alert.alert("Coming soon", `${label} isn't built in the app yet — manage it on the website.`);
  };

  return (
    <View style={styles.screen}>
      <View style={styles.topBar}>
        <TouchableOpacity style={styles.backButton} onPress={() => navigation.goBack()}>
          <Text style={styles.backIcon}>‹</Text>
        </TouchableOpacity>
        <View>
          <Text style={styles.title}>Setup</Text>
          <Text style={styles.subtitle}>
            {overview ? `${overview.tenant_name} · ${planLabel(overview.plan)} plan` : "Loading…"}
          </Text>
        </View>
      </View>

      <ScrollView style={styles.body} contentContainerStyle={styles.bodyContent}>
        {error ? <Text style={styles.error}>{error}</Text> : null}
        {!overview && !error ? <ActivityIndicator color={colors.teal} style={{ marginTop: 16 }} /> : null}

        {overview ? (
          <View style={styles.planCard}>
            <View style={styles.planHeader}>
              <Text style={styles.planTitle}>
                Plan: <Text style={styles.planName}>{planLabel(overview.plan)}</Text>
              </Text>
              <Text style={styles.planUpgrade}>Upgrade →</Text>
            </View>
            <View style={styles.planGrid}>
              {overview.plan_usage.map((pu) => {
                const pct = pu.limit ? Math.min(Math.round((pu.used / pu.limit) * 100), 100) : 0;
                const valueColor = pct >= 100 ? "#f87171" : pct >= 80 ? "#fbbf24" : "#34d399";
                const barColor = pct >= 100 ? "#ef4444" : pct >= 80 ? "#f59e0b" : "#3b82f6";
                return (
                  <View key={pu.label} style={styles.planUsageItem}>
                    <View style={styles.planUsageRow}>
                      <Text style={styles.planUsageLabel}>{pu.label}</Text>
                      <Text style={[styles.planUsageValue, { color: valueColor }]}>
                        {pu.limit ? `${pu.used}/${pu.limit}` : `${pu.used} / ∞`}
                      </Text>
                    </View>
                    <View style={styles.planBarTrack}>
                      <View style={[styles.planBarFill, { width: `${pu.limit ? pct : 0}%`, backgroundColor: barColor }]} />
                    </View>
                  </View>
                );
              })}
            </View>
          </View>
        ) : null}

        {overview?.sections.map((sec) => (
          <View key={sec.title}>
            <Text style={styles.sectionTitle}>{sec.title}</Text>
            <View style={styles.sectionCard}>
              {sec.rows.map((row, idx) => (
                <TouchableOpacity
                  key={row.key}
                  style={[styles.row, idx !== 0 && styles.rowBorder]}
                  onPress={() => onRowPress(row.key, row.label)}
                >
                  <View style={styles.rowIcon}>
                    <Text style={{ fontSize: 15 }}>{row.icon}</Text>
                  </View>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.rowLabel}>{row.label}</Text>
                    <Text style={styles.rowSub}>{row.sub}</Text>
                  </View>
                  <Text style={styles.rowChevron}>›</Text>
                </TouchableOpacity>
              ))}
            </View>
          </View>
        ))}
      </ScrollView>
    </View>
  );
}

function planLabel(plan: string): string {
  return plan.charAt(0) + plan.slice(1).toLowerCase();
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
    body: { flex: 1 },
    bodyContent: { padding: 20, paddingBottom: 40 },
    error: { color: "#f87185", fontSize: 13, marginBottom: 8 },
    planCard: { backgroundColor: colors.cardBg, borderWidth: 1, borderColor: colors.border, borderRadius: 16, padding: 16, marginBottom: 18 },
    planHeader: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: 12 },
    planTitle: { fontSize: 13, fontWeight: "700", color: colors.textPrimary },
    planName: { color: "#a99cf7" },
    planUpgrade: { fontSize: 11, fontWeight: "700", color: colors.indigo },
    planGrid: { flexDirection: "row", flexWrap: "wrap", gap: 12 },
    planUsageItem: { width: "47%" },
    planUsageRow: { flexDirection: "row", justifyContent: "space-between", marginBottom: 4 },
    planUsageLabel: { fontSize: 10.5, color: colors.textMuted },
    planUsageValue: { fontSize: 10.5, fontWeight: "700" },
    planBarTrack: { height: 4, borderRadius: 99, backgroundColor: colors.border, overflow: "hidden" },
    planBarFill: { height: "100%", borderRadius: 99 },
    sectionTitle: { fontSize: 11, fontWeight: "700", letterSpacing: 0.5, textTransform: "uppercase", color: colors.textMuted, marginHorizontal: 4, marginBottom: 8, marginTop: 4 },
    sectionCard: { backgroundColor: colors.cardBg, borderWidth: 1, borderColor: colors.border, borderRadius: 14, marginBottom: 18, overflow: "hidden" },
    row: { flexDirection: "row", alignItems: "center", gap: 12, paddingVertical: 13, paddingHorizontal: 14 },
    rowBorder: { borderTopWidth: 1, borderColor: colors.border },
    rowIcon: {
      width: 32, height: 32, borderRadius: 9, backgroundColor: "rgba(148,163,184,0.14)",
      alignItems: "center", justifyContent: "center", flexShrink: 0,
    },
    rowLabel: { fontSize: 13, fontWeight: "700", color: colors.textPrimary },
    rowSub: { fontSize: 11, color: colors.textSecondary, marginTop: 2 },
    rowChevron: { fontSize: 16, color: colors.textMuted, flexShrink: 0 },
  });
}
