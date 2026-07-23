import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Alert, ScrollView, StyleSheet, Switch, Text, TouchableOpacity, View } from "react-native";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";

import type { AuthStackParamList } from "../navigation/AuthNavigator";
import { ApiError } from "../api/client";
import { flowsApi, type Flow } from "../api/setup";
import { useTheme, type ThemeColors } from "../theme/ThemeContext";

type Props = NativeStackScreenProps<AuthStackParamList, "SetupFlows">;

export default function FlowsScreen({ navigation }: Props) {
  const { colors } = useTheme();
  const styles = makeStyles(colors);
  const [flows, setFlows] = useState<Flow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const rows = await flowsApi.list();
      setFlows(rows);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Couldn't load flows.");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const toggle = async (flow: Flow) => {
    setFlows((prev) => prev?.map((f) => (f.id === flow.id ? { ...f, is_active: !f.is_active } : f)) ?? prev);
    try {
      await flowsApi.setActive(flow.id, !flow.is_active);
    } catch (e) {
      Alert.alert("Couldn't update", e instanceof ApiError ? e.detail : "Something went wrong.");
      await load();
    }
  };

  return (
    <View style={styles.screen}>
      <View style={styles.topBar}>
        <TouchableOpacity style={styles.backButton} onPress={() => navigation.goBack()}>
          <Text style={styles.backIcon}>‹</Text>
        </TouchableOpacity>
        <View>
          <Text style={styles.title}>Flows</Text>
          <Text style={styles.subtitle}>Building &amp; editing stages is desktop-only</Text>
        </View>
      </View>

      {!flows && !error ? <ActivityIndicator color={colors.teal} style={{ marginTop: 24 }} /> : null}
      {error ? <Text style={styles.error}>{error}</Text> : null}

      <ScrollView style={styles.body} contentContainerStyle={styles.bodyContent}>
        {/* Flow stages/routing/custom fields are edited on the website only —
            the app can view flows and flip Active/Inactive, nothing more.
            Keep this banner if you're tempted to add an edit affordance here. */}
        <View style={styles.webOnlyBanner}>
          <Text style={styles.webOnlyBannerText}>
            ✏️ Editable from web only — build and edit stages, routing & custom fields on the website. The app can view flows and turn them on/off.
          </Text>
        </View>

        {flows && flows.length === 0 ? <Text style={styles.empty}>No flows yet — build one on the website.</Text> : null}
        {flows?.map((f) => (
          <View key={f.id} style={styles.row}>
            <View style={[styles.colorDot, { backgroundColor: f.color }]} />
            <View style={{ flex: 1, minWidth: 0 }}>
              <Text style={styles.rowTitle}>{f.name}</Text>
              <Text style={styles.rowSubtitle}>{f.stage_count} stages{f.description ? ` · ${f.description}` : ""}</Text>
            </View>
            <Switch
              value={f.is_active}
              onValueChange={() => toggle(f)}
              trackColor={{ false: colors.border, true: colors.teal }}
              thumbColor="#fff"
            />
          </View>
        ))}
      </ScrollView>
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
    empty: { color: colors.textMuted, fontSize: 13, textAlign: "center", marginTop: 30 },
    webOnlyBanner: {
      backgroundColor: "rgba(234,179,8,0.1)", borderWidth: 1, borderColor: "rgba(234,179,8,0.3)",
      borderRadius: 12, padding: 12, marginBottom: 14,
    },
    webOnlyBannerText: { fontSize: 11.5, color: colors.textSecondary, lineHeight: 16 },
    row: { flexDirection: "row", alignItems: "center", gap: 12, backgroundColor: colors.cardBg, borderWidth: 1, borderColor: colors.border, borderRadius: 14, padding: 14, marginBottom: 10 },
    colorDot: { width: 10, height: 10, borderRadius: 5, flexShrink: 0 },
    rowTitle: { fontSize: 13.5, fontWeight: "700", color: colors.textPrimary },
    rowSubtitle: { fontSize: 11.5, color: colors.textSecondary, marginTop: 2 },
  });
}
