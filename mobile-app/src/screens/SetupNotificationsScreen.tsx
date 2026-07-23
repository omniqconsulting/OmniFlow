import { useCallback, useEffect, useMemo, useState } from "react";
import { ActivityIndicator, ScrollView, StyleSheet, Switch, Text, TouchableOpacity, View } from "react-native";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";

import type { AuthStackParamList } from "../navigation/AuthNavigator";
import { ApiError } from "../api/client";
import {
  getNotificationRules,
  getNotificationSettings,
  updateNotificationRules,
  updateNotificationSettings,
  type NotificationCondition,
  type NotificationRule,
  type NotificationSettings,
} from "../api/setup";
import { useTheme, type ThemeColors } from "../theme/ThemeContext";

type Props = NativeStackScreenProps<AuthStackParamList, "SetupNotifications">;

const DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export default function SetupNotificationsScreen({ navigation }: Props) {
  const { colors } = useTheme();
  const styles = makeStyles(colors);
  const [settings, setSettings] = useState<NotificationSettings | null>(null);
  const [conditions, setConditions] = useState<NotificationCondition[]>([]);
  const [rules, setRules] = useState<NotificationRule[]>([]);
  const [availableRoles, setAvailableRoles] = useState<string[]>([]);
  const [roleLabels, setRoleLabels] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [rulesSaving, setRulesSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const [data, rulesData] = await Promise.all([getNotificationSettings(), getNotificationRules()]);
      setSettings(data);
      setConditions(rulesData.conditions);
      setRules(rulesData.rules);
      setAvailableRoles(rulesData.available_roles);
      setRoleLabels(rulesData.role_labels);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Couldn't load notification settings.");
    }
  }, []);

  const rulesByCategory = useMemo(() => {
    const order = ["Checklist", "Delegation", "FMS"];
    const groups: Record<string, NotificationCondition[]> = {};
    for (const c of conditions) {
      (groups[c.category] ??= []).push(c);
    }
    return order.filter((cat) => groups[cat]?.length).map((cat) => ({ category: cat, items: groups[cat] }));
  }, [conditions]);

  const toggleRuleChannel = (conditionKey: string, channel: "in_app" | "push" | "whatsapp") => {
    setRules((prev) => prev.map((r) => (r.condition_key === conditionKey ? { ...r, [channel]: !r[channel] } : r)));
  };

  const toggleRuleRecipient = (conditionKey: string, role: string) => {
    setRules((prev) =>
      prev.map((r) => {
        if (r.condition_key !== conditionKey) return r;
        const has = r.recipients.includes(role);
        return { ...r, recipients: has ? r.recipients.filter((x) => x !== role) : [...r.recipients, role] };
      })
    );
  };

  const saveRules = async () => {
    setRulesSaving(true);
    try {
      const updated = await updateNotificationRules(rules);
      setRules(updated.rules);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Couldn't save notification rules.");
    } finally {
      setRulesSaving(false);
    }
  };

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
              <Text style={styles.cardTitle}>Notification Rules</Text>
              <Text style={styles.cardDesc}>
                Turn each notification on or off, per channel — fully customizable.
              </Text>
              {rulesByCategory.map((group) => (
                <View key={group.category} style={{ marginTop: 12 }}>
                  <Text style={styles.ruleCategoryLabel}>{group.category}</Text>
                  {group.items.map((cond) => {
                    const rule = rules.find((r) => r.condition_key === cond.key);
                    if (!rule) return null;
                    return (
                      <View key={cond.key} style={styles.ruleRow}>
                        <Text style={styles.ruleLabel}>{cond.label}</Text>
                        <Text style={styles.ruleMeta}>{cond.cadence}</Text>
                        <Text style={styles.ruleRecipientsHeading}>Recipients</Text>
                        <View style={styles.ruleChannels}>
                          {availableRoles.map((role) => (
                            <RuleChannelToggle
                              key={role}
                              label={roleLabels[role] || role}
                              value={rule.recipients.includes(role)}
                              onToggle={() => toggleRuleRecipient(cond.key, role)}
                              colors={colors}
                            />
                          ))}
                        </View>
                        <Text style={[styles.ruleRecipientsHeading, { marginTop: 8 }]}>Channels</Text>
                        <View style={styles.ruleChannels}>
                          <RuleChannelToggle label="In-App" value={rule.in_app} onToggle={() => toggleRuleChannel(cond.key, "in_app")} colors={colors} />
                          <RuleChannelToggle label="Push" value={rule.push} onToggle={() => toggleRuleChannel(cond.key, "push")} colors={colors} />
                          <RuleChannelToggle label="WhatsApp" value={rule.whatsapp} onToggle={() => toggleRuleChannel(cond.key, "whatsapp")} colors={colors} />
                        </View>
                      </View>
                    );
                  })}
                </View>
              ))}
              <TouchableOpacity style={[styles.rulesSaveButton, rulesSaving && { opacity: 0.7 }]} onPress={saveRules} disabled={rulesSaving}>
                {rulesSaving ? <ActivityIndicator color="#fff" /> : <Text style={styles.saveButtonText}>Save Rules</Text>}
              </TouchableOpacity>
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

function RuleChannelToggle({
  label, value, onToggle, colors,
}: {
  label: string; value: boolean; onToggle: () => void; colors: ThemeColors;
}) {
  return (
    <TouchableOpacity
      style={[
        ruleToggleStyles.chip,
        { backgroundColor: value ? colors.teal + "26" : colors.screenBg, borderColor: value ? colors.teal : colors.border },
      ]}
      onPress={onToggle}
    >
      <Text style={[ruleToggleStyles.chipText, { color: value ? colors.teal : colors.textMuted }]}>{label}</Text>
    </TouchableOpacity>
  );
}

const ruleToggleStyles = StyleSheet.create({
  chip: { paddingVertical: 5, paddingHorizontal: 9, borderRadius: 999, borderWidth: 1 },
  chipText: { fontSize: 10.5, fontWeight: "700" },
});

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
    ruleCategoryLabel: { fontSize: 11, fontWeight: "700", textTransform: "uppercase", letterSpacing: 0.5, color: colors.textMuted, marginBottom: 8 },
    ruleRow: { paddingVertical: 10, borderTopWidth: 1, borderColor: colors.border },
    ruleLabel: { fontSize: 12.5, fontWeight: "700", color: colors.textPrimary },
    ruleMeta: { fontSize: 10.5, color: colors.textMuted, marginTop: 2, marginBottom: 8 },
    ruleRecipientsHeading: { fontSize: 9.5, fontWeight: "700", textTransform: "uppercase", letterSpacing: 0.4, color: colors.textMuted, marginBottom: 5 },
    ruleChannels: { flexDirection: "row", gap: 7 },
    rulesSaveButton: { height: 44, borderRadius: 12, backgroundColor: colors.indigo, alignItems: "center", justifyContent: "center", marginTop: 16 },
  });
}
