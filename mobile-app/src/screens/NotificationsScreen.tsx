import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, AppState, ScrollView, StyleSheet, Text, TouchableOpacity, View } from "react-native";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";

import type { AuthStackParamList } from "../navigation/AuthNavigator";
import { ApiError } from "../api/client";
import {
  deleteNotification,
  listNotifications,
  markAllNotificationsRead,
  markNotificationRead,
  type NotificationItem,
} from "../api/notifications";
import { CATEGORY_COLOR, type NavCategory } from "../config/roleNav";
import SwipeToDeleteRow from "../components/SwipeToDeleteRow";
import { enablePushNotifications, getPushPermissionStatus, type PushPermissionStatus } from "../notifications/push";
import { useTheme, type ThemeColors } from "../theme/ThemeContext";

type Props = NativeStackScreenProps<AuthStackParamList, "Notifications">;

export default function NotificationsScreen({ navigation, route }: Props) {
  const { user } = route.params;
  const { colors } = useTheme();
  const styles = makeStyles(colors);
  const [items, setItems] = useState<NotificationItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pushStatus, setPushStatus] = useState<PushPermissionStatus | null>(null);
  const [enabling, setEnabling] = useState(false);

  const load = useCallback(async () => {
    try {
      const page = await listNotifications();
      setItems(page.items);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Couldn't load notifications.");
    }
  }, []);

  const refreshPushStatus = useCallback(() => {
    getPushPermissionStatus().then(setPushStatus);
  }, []);

  useEffect(() => {
    load();
    refreshPushStatus();
    // If the "enable" button sent the user to the device's Settings app,
    // re-check when they come back — there's no other signal that they've
    // toggled the OS permission.
    const sub = AppState.addEventListener("change", (state) => {
      if (state === "active") refreshPushStatus();
    });
    return () => sub.remove();
  }, [load, refreshPushStatus]);

  const onEnablePush = async () => {
    setEnabling(true);
    try {
      const status = await enablePushNotifications();
      setPushStatus(status);
    } finally {
      setEnabling(false);
    }
  };

  const today = items?.filter((n) => n.day === "today") ?? [];
  const earlier = items?.filter((n) => n.day === "earlier") ?? [];
  const hasUnread = (items ?? []).some((n) => !n.is_read);

  const markAllRead = async () => {
    setItems((prev) => prev?.map((n) => ({ ...n, is_read: true })) ?? prev);
    try {
      await markAllNotificationsRead();
    } catch {
      await load();
    }
  };

  const openNotif = async (n: NotificationItem) => {
    if (!n.is_read) {
      setItems((prev) => prev?.map((x) => (x.id === n.id ? { ...x, is_read: true } : x)) ?? prev);
      markNotificationRead(n.id).catch(() => {});
    }
    if (n.link_type === "ticket" && n.link_id) {
      navigation.navigate("TicketDetail", { user, ticketId: n.link_id });
    }
    // link_type "none" — no native screen for that destination yet; marking
    // read is the only action available, same as tapping an unbuilt tile.
  };

  const dismiss = async (id: string) => {
    setItems((prev) => prev?.filter((n) => n.id !== id) ?? prev);
    try {
      await deleteNotification(id);
    } catch {
      await load();
    }
  };

  const renderRow = (n: NotificationItem) => {
    const catColors = CATEGORY_COLOR[(n.cat as NavCategory) ?? "op"];
    return (
      <SwipeToDeleteRow key={n.id} onDismiss={() => dismiss(n.id)}>
        <TouchableOpacity style={styles.row} onPress={() => openNotif(n)} activeOpacity={0.8}>
          <View style={[styles.dot, !n.is_read && styles.dotUnread]} />
          <View style={[styles.iconWrap, { backgroundColor: catColors.bg }]}>
            <Text style={{ fontSize: 14 }}>{n.icon}</Text>
          </View>
          <View style={{ flex: 1, minWidth: 0 }}>
            <Text style={[styles.rowTitle, n.is_read ? styles.titleRead : styles.titleUnread]} numberOfLines={1}>
              {n.title}
            </Text>
            <Text style={styles.meta}>{n.body || n.meta}</Text>
          </View>
          <Text style={styles.rel}>{n.rel}</Text>
        </TouchableOpacity>
      </SwipeToDeleteRow>
    );
  };

  return (
    <View style={styles.screen}>
      <View style={styles.topBar}>
        <View style={styles.topBarLeft}>
          <TouchableOpacity style={styles.backButton} onPress={() => navigation.goBack()}>
            <Text style={styles.backIcon}>‹</Text>
          </TouchableOpacity>
          <Text style={styles.title}>Notifications</Text>
        </View>
        {hasUnread ? (
          <TouchableOpacity onPress={markAllRead}>
            <Text style={styles.markAllRead}>Mark all read</Text>
          </TouchableOpacity>
        ) : null}
      </View>

      {!items && !error ? <ActivityIndicator color={colors.teal} style={{ marginTop: 24 }} /> : null}
      {error ? <Text style={styles.error}>{error}</Text> : null}

      <ScrollView style={styles.body} contentContainerStyle={styles.bodyContent}>
        {pushStatus === "denied" || pushStatus === "undetermined" ? (
          <View style={styles.pushBanner}>
            <View style={{ flex: 1 }}>
              <Text style={styles.pushBannerTitle}>Push notifications are off</Text>
              <Text style={styles.pushBannerBody}>
                {pushStatus === "denied"
                  ? "Turn them on in your device Settings to get alerts outside the app."
                  : "Enable them to get alerts even when the app is closed."}
              </Text>
            </View>
            <TouchableOpacity style={styles.pushBannerButton} onPress={onEnablePush} disabled={enabling}>
              {enabling ? (
                <ActivityIndicator color="#fff" size="small" />
              ) : (
                <Text style={styles.pushBannerButtonText}>{pushStatus === "denied" ? "Open Settings" : "Enable"}</Text>
              )}
            </TouchableOpacity>
          </View>
        ) : null}

        {today.length > 0 ? (
          <>
            <Text style={styles.sectionLabel}>Today</Text>
            {today.map(renderRow)}
          </>
        ) : null}

        {earlier.length > 0 ? (
          <>
            <Text style={[styles.sectionLabel, { marginTop: today.length > 0 ? 16 : 0 }]}>Earlier</Text>
            {earlier.map(renderRow)}
          </>
        ) : null}

        {items && items.length === 0 ? (
          <View style={styles.empty}>
            <Text style={styles.emptyIcon}>🔔</Text>
            <Text style={styles.emptyText}>You're all caught up.</Text>
          </View>
        ) : null}
      </ScrollView>
    </View>
  );
}

function makeStyles(colors: ThemeColors) {
  return StyleSheet.create({
    screen: { flex: 1, backgroundColor: colors.screenBg },
    topBar: { paddingTop: 58, paddingHorizontal: 20, paddingBottom: 10, flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
    topBarLeft: { flexDirection: "row", alignItems: "center", gap: 12 },
    backButton: { width: 34, height: 34, borderRadius: 10, backgroundColor: colors.cardBg, borderWidth: 1, borderColor: colors.border, alignItems: "center", justifyContent: "center" },
    backIcon: { fontSize: 16, color: colors.textSecondary },
    title: { fontSize: 16, fontWeight: "800", color: colors.textPrimary },
    markAllRead: { fontSize: 11.5, fontWeight: "700", color: colors.indigo },
    body: { flex: 1 },
    bodyContent: { padding: 20, paddingBottom: 40 },
    error: { color: "#f87185", fontSize: 13, marginBottom: 8 },
    pushBanner: {
      flexDirection: "row", alignItems: "center", gap: 12, backgroundColor: "rgba(234,179,8,0.1)",
      borderWidth: 1, borderColor: "rgba(234,179,8,0.3)", borderRadius: 14, padding: 14, marginBottom: 16,
    },
    pushBannerTitle: { fontSize: 12.5, fontWeight: "700", color: colors.textPrimary },
    pushBannerBody: { fontSize: 11, color: colors.textSecondary, marginTop: 2, lineHeight: 15 },
    pushBannerButton: { backgroundColor: colors.indigo, borderRadius: 10, paddingVertical: 9, paddingHorizontal: 12, minWidth: 88, alignItems: "center" },
    pushBannerButtonText: { fontSize: 11.5, fontWeight: "700", color: "#fff" },
    sectionLabel: { fontSize: 11, fontWeight: "700", letterSpacing: 0.5, textTransform: "uppercase", color: colors.textMuted, marginHorizontal: 4, marginBottom: 8 },
    row: { flexDirection: "row", alignItems: "center", gap: 11, padding: 13, backgroundColor: colors.cardBg, borderWidth: 1, borderColor: colors.border, borderRadius: 14 },
    dot: { width: 7, height: 7, borderRadius: 4, backgroundColor: "transparent", flexShrink: 0 },
    dotUnread: { backgroundColor: colors.teal },
    iconWrap: { width: 32, height: 32, borderRadius: 9, alignItems: "center", justifyContent: "center", flexShrink: 0 },
    rowTitle: { fontSize: 12.5 },
    titleRead: { fontWeight: "500", color: colors.textSecondary },
    titleUnread: { fontWeight: "700", color: colors.textPrimary },
    meta: { fontSize: 11, color: colors.textMuted, marginTop: 2 },
    rel: { fontSize: 10.5, color: colors.textMuted, flexShrink: 0 },
    empty: { alignItems: "center", paddingVertical: 60 },
    emptyIcon: { fontSize: 30, marginBottom: 10 },
    emptyText: { fontSize: 13, fontWeight: "600", color: colors.textSecondary },
  });
}
