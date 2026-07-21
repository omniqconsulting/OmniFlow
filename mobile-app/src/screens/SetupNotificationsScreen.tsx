import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, ScrollView, StyleSheet, Switch, Text, TouchableOpacity, View } from "react-native";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";

import type { AuthStackParamList } from "../navigation/AuthNavigator";
import { ApiError } from "../api/client";
import { getNotificationSettings, updateNotificationSettings, type NotificationSettings } from "../api/setup";
import { useTheme, type ThemeColors } from "../theme/ThemeContext";

type Props = NativeStackScreenProps<AuthStackParamList, "SetupNotifications">;

const DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

const WA_TOGGLES: { key: keyof NotificationSettings; label: string }[] = [
  { key: "wa_notif_ticket_assigned", label: "Ticket assigned" },
  { key: "wa_notif_ticket_escalated", label: "Ticket escalated / flagged" },
  { key: "wa_notif_fms_ticket_created", label: "FMS ticket created" },
];

export default function SetupNotificationsScreen({ navigation }: Props) {
  const { colors } = useTheme();
  const styles = makeStyles(colors);
  const [settings, setSettings] = useState<NotificationSettings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const data = await getNotificationSettings();
      setSettings(data);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Couldn't load notification settings.");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const toggle = (key: keyof NotificationSettings) => {
    setSettings((prev) => (prev ? { ...prev, [key]: !prev[key] } : prev));
  };

  const save = async () => {
    if (!settings) return;
    setSaving(true);
    try {
      const updated = await updateNotificationSettings({
        suppress_notif_outside_hours: settings.suppress_notif_outside_hours,
        wa_notif_ticket_assigned: settings.wa_notif_ticket_assigned,
        wa_notif_ticket_escalated: settings.wa_notif_ticket_escalated,
        wa_notif_fms_ticket_created: settings.wa_notif_fms_ticket_created,
      });
      setSettings(updated);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Couldn't save notification settings.");
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
          <Text style={styles.title}>Notifications</Text>
          <Text style={styles.subtitle}>Office hours, alerts &amp; delivery</Text>
        </View>
      </View>

      {!settings && !error ? <ActivityIndicator color={colors.teal} style={{ marginTop: 24 }} /> : null}
      {error ? <Text style={styles.error}>{error}</Text> : null}

      {settings ? (
        <>
          <ScrollView style={styles.body} contentContainerStyle={styles.bodyContent}>
            <View style={styles.card}>
              <Text style={styles.cardTitle}>Office Hours</Text>
              <View style={styles.hoursRow}>
                <View style={styles.hourBox}>
                  <Text style={styles.hourLabel}>Work starts</Text>
                  <Text style={styles.hourValue}>{settings.work_start_time}</Text>
                </View>
                <View style={styles.hourBox}>
                  <Text style={styles.hourLabel}>Work ends</Text>
                  <Text style={styles.hourValue}>{settings.work_end_time}</Text>
                </View>
              </View>
              <View style={styles.dayChips}>
                {DAY_LABELS.map((label, i) => {
                  const active = settings.work_days.includes(i);
                  return (
                    <View key={label} style={[styles.dayChip, active ? styles.dayChipActive : styles.dayChipInactive]}>
                      <Text style={[styles.dayChipText, active ? styles.dayChipTextActive : styles.dayChipTextInactive]}>
                        {label}
                      </Text>
                    </View>
                  );
                })}
              </View>
              <View style={styles.suppressRow}>
                <Text style={styles.suppressLabel}>Suppress notifications outside office hours</Text>
                <Switch
                  value={settings.suppress_notif_outside_hours}
                  onValueChange={() => toggle("suppress_notif_outside_hours")}
                  trackColor={{ false: colors.border, true: colors.teal }}
                  thumbColor="#ffffff"
                />
              </View>
            </View>

            <View style={styles.card}>
              <Text style={styles.cardTitle}>Ticket TaT Alerts</Text>
              <View style={styles.hoursRow}>
                <View style={styles.hourBox}>
                  <Text style={styles.hourLabel}>Manager + employee at</Text>
                  <Text style={styles.hourValue}>{settings.ticket_notif_tat_pct}%</Text>
                </View>
                <View style={styles.hourBox}>
                  <Text style={styles.hourLabel}>Admin + manager at</Text>
                  <Text style={styles.hourValue}>{settings.ticket_notif_tat_pct_both}%</Text>
                </View>
              </View>
            </View>

            <View style={styles.card}>
              <Text style={styles.cardTitle}>Checklist Reminders</Text>
              <View style={styles.hourChipRow}>
                {settings.checklist_notif_hours.map((h) => (
                  <View key={h} style={styles.hourChip}>
                    <Text style={styles.hourChipText}>{String(h).padStart(2, "0")}:00 IST</Text>
                  </View>
                ))}
              </View>
            </View>

            <View style={styles.card}>
              <Text style={styles.cardTitle}>WhatsApp Delivery</Text>
              <Text style={styles.cardDesc}>
                Adds a WhatsApp send alongside the in-app notification, for opted-in employees.
              </Text>
              {WA_TOGGLES.map((wa, idx) => (
                <View key={wa.key} style={[styles.waRow, idx !== 0 && styles.waRowBorder]}>
                  <Text style={styles.waLabel}>{wa.label}</Text>
                  <Switch
                    value={Boolean(settings[wa.key])}
                    onValueChange={() => toggle(wa.key)}
                    trackColor={{ false: colors.border, true: colors.teal }}
                    thumbColor="#ffffff"
                  />
                </View>
              ))}
            </View>
          </ScrollView>

          <View style={styles.saveBar}>
            <TouchableOpacity style={[styles.saveButton, saving && { opacity: 0.7 }]} onPress={save} disabled={saving}>
              {saving ? <ActivityIndicator color="#fff" /> : <Text style={styles.saveButtonText}>Save Settings</Text>}
            </TouchableOpacity>
          </View>
        </>
      ) : null}
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
    body: { flex: 1 },
    bodyContent: { paddingHorizontal: 20, paddingTop: 8, paddingBottom: 110 },
    error: { color: "#f87185", fontSize: 13, marginHorizontal: 20, marginTop: 12 },
    card: { backgroundColor: colors.cardBg, borderWidth: 1, borderColor: colors.border, borderRadius: 14, padding: 16, marginBottom: 14 },
    cardTitle: { fontSize: 13, fontWeight: "700", color: colors.textPrimary, marginBottom: 10 },
    cardDesc: { fontSize: 11, color: colors.textMuted, marginBottom: 10, lineHeight: 16 },
    hoursRow: { flexDirection: "row", gap: 10 },
    hourBox: { flex: 1, backgroundColor: colors.screenBg, borderWidth: 1, borderColor: colors.border, borderRadius: 10, padding: 12 },
    hourLabel: { fontSize: 10, color: colors.textMuted, marginBottom: 3 },
    hourValue: { fontSize: 13, fontWeight: "700", color: colors.textPrimary },
    dayChips: { flexDirection: "row", flexWrap: "wrap", gap: 6, marginTop: 10 },
    dayChip: { paddingVertical: 6, paddingHorizontal: 10, borderRadius: 8 },
    dayChipActive: { backgroundColor: "rgba(102,87,242,0.16)" },
    dayChipInactive: { backgroundColor: colors.border },
    dayChipText: { fontSize: 11, fontWeight: "700" },
    dayChipTextActive: { color: "#a99cf7" },
    dayChipTextInactive: { color: colors.textMuted },
    suppressRow: {
      flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginTop: 14, paddingTop: 14,
      borderTopWidth: 1, borderColor: colors.border,
    },
    suppressLabel: { fontSize: 12, color: colors.textSecondary, maxWidth: 230, lineHeight: 17 },
    hourChipRow: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
    hourChip: { backgroundColor: "rgba(102,87,242,0.14)", borderRadius: 8, paddingVertical: 5, paddingHorizontal: 10 },
    hourChipText: { fontSize: 11, fontWeight: "700", color: "#a99cf7" },
    waRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingVertical: 9 },
    waRowBorder: { borderTopWidth: 1, borderColor: colors.border },
    waLabel: { fontSize: 12.5, color: colors.textPrimary },
    saveBar: {
      position: "absolute", left: 0, right: 0, bottom: 0, paddingHorizontal: 20, paddingTop: 12, paddingBottom: 20,
      backgroundColor: colors.tabBarBg, borderTopWidth: 1, borderColor: colors.border,
    },
    saveButton: { height: 48, borderRadius: 12, backgroundColor: colors.indigo, alignItems: "center", justifyContent: "center" },
    saveButtonText: { fontSize: 14, fontWeight: "700", color: "#fff" },
  });
}
