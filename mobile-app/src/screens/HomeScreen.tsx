import { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Animated,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";

import type { AuthStackParamList } from "../navigation/AuthNavigator";
import { getHomeSummary, type HomeSummary } from "../api/home";
import { ApiError } from "../api/client";
import {
  BOTTOM_COUNT,
  BUILT_SCREENS,
  CATEGORY_COLOR,
  FULL_NAV,
  GREETING_SUB,
  NAV_DEF,
  TILES,
  timeOfDayGreeting,
  type NavId,
} from "../config/roleNav";

const TEAL = "#2DD4BF";
const INDIGO = "#6657F2";

type Props = NativeStackScreenProps<AuthStackParamList, "Home">;

export default function HomeScreen({ navigation, route }: Props) {
  const { user, slug } = route.params;
  const [summary, setSummary] = useState<HomeSummary | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const drawerX = useRef(new Animated.Value(-260)).current;

  const load = useCallback(async () => {
    try {
      const s = await getHomeSummary();
      setSummary(s);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Couldn't load your Home summary.");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    Animated.timing(drawerX, {
      toValue: drawerOpen ? 0 : -260,
      duration: 260,
      useNativeDriver: true,
    }).start();
  }, [drawerOpen, drawerX]);

  const onRefresh = async () => {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  };

  const role = user.role in TILES ? user.role : "EMPLOYEE";
  const tiles = TILES[role] ?? [];
  const fullNav = FULL_NAV[role] ?? FULL_NAV.EMPLOYEE;
  const bottomCount = BOTTOM_COUNT[role] ?? 4;
  const bottomTabs = fullNav.slice(0, bottomCount);

  const goTo = (navId: NavId) => {
    setDrawerOpen(false);
    const screen = BUILT_SCREENS[navId];
    if (!screen) {
      Alert.alert("Coming soon", `${NAV_DEF[navId].label} isn't built in the app yet.`);
      return;
    }
    if (screen === "Home") return; // already here
    if (screen === "Attendance") navigation.navigate("Attendance", { user });
  };

  return (
    <View style={styles.screen}>
      <View style={styles.topBar}>
        <View style={styles.brandRow}>
          <LinearGradient colors={[TEAL, INDIGO]} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={styles.brandMark} />
          <Text style={styles.workspaceLabel}>{slug}</Text>
        </View>
        <View style={styles.topBarRight}>
          <TouchableOpacity
            style={styles.iconButton}
            onPress={() => Alert.alert("Notifications", "Notification detail isn't built in the app yet.")}
          >
            <Text style={styles.iconButtonText}>🔔</Text>
            {summary && summary.unread_notifications > 0 ? (
              <View style={styles.badge}>
                <Text style={styles.badgeText}>{summary.unread_notifications}</Text>
              </View>
            ) : null}
          </TouchableOpacity>
          <View style={styles.avatar}>
            <Text style={styles.avatarText}>{user.name.slice(0, 1).toUpperCase()}</Text>
          </View>
        </View>
      </View>

      <ScrollView
        style={styles.body}
        contentContainerStyle={styles.bodyContent}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={TEAL} />}
      >
        <Text style={styles.greeting}>{timeOfDayGreeting()}, {user.name}</Text>
        <Text style={styles.greetingSub}>{GREETING_SUB[role]}</Text>

        {error ? <Text style={styles.error}>{error}</Text> : null}
        {!summary && !error ? <ActivityIndicator color={TEAL} style={{ marginTop: 16 }} /> : null}

        {summary ? (
          <View style={styles.statsRow}>
            <View style={styles.statChip}>
              <Text style={styles.statNumber}>{summary.open_tickets}</Text>
              <Text style={styles.statLabel}>Open tickets</Text>
            </View>
            <View style={styles.statChip}>
              <Text style={styles.statNumber}>{summary.open_checklists}</Text>
              <Text style={styles.statLabel}>Open checklists</Text>
            </View>
          </View>
        ) : null}

        <View style={styles.tileGrid}>
          {tiles.map((tile) => {
            const colors = CATEGORY_COLOR[tile.cat];
            const built = !!BUILT_SCREENS[tile.nav];
            return (
              <TouchableOpacity
                key={tile.title}
                style={[styles.tile, !built && styles.tileDisabled]}
                onPress={() => goTo(tile.nav)}
              >
                <View style={[styles.tileIcon, { backgroundColor: colors.bg }]}>
                  <Text style={{ fontSize: 19 }}>{tile.icon}</Text>
                </View>
                <Text style={styles.tileTitle}>{tile.title}</Text>
                <Text style={styles.tileSub}>{tile.sub}</Text>
                {!built ? <Text style={styles.tileComingSoon}>Coming soon</Text> : null}
              </TouchableOpacity>
            );
          })}
        </View>

        <View style={styles.activityCard}>
          <Text style={styles.activityTitle}>Recent activity</Text>
          <Text style={styles.activityPlaceholder}>
            Not available yet — there's no activity-feed endpoint in the API today. This section will populate once
            that's built.
          </Text>
        </View>
      </ScrollView>

      <View style={styles.tabBar}>
        {bottomTabs.map((navId) => {
          const def = NAV_DEF[navId];
          const active = !drawerOpen && navId === "home";
          const color = active ? TEAL : "#64748b";
          return (
            <TouchableOpacity key={navId} style={styles.tabItem} onPress={() => goTo(navId)}>
              <Text style={[styles.tabIcon, { color }]}>{def.icon}</Text>
              <Text style={[styles.tabLabel, { color }]}>{def.label}</Text>
            </TouchableOpacity>
          );
        })}
        <TouchableOpacity style={styles.tabItem} onPress={() => setDrawerOpen((v) => !v)}>
          <Text style={[styles.tabIcon, { color: drawerOpen ? TEAL : "#64748b" }]}>☰</Text>
          <Text style={[styles.tabLabel, { color: drawerOpen ? TEAL : "#64748b" }]}>Menu</Text>
        </TouchableOpacity>
      </View>

      {drawerOpen ? (
        <TouchableOpacity style={styles.backdrop} activeOpacity={1} onPress={() => setDrawerOpen(false)} />
      ) : null}
      <Animated.View style={[styles.drawer, { transform: [{ translateX: drawerX }] }]}>
        <View style={styles.drawerHeader}>
          <Text style={styles.drawerTitle}>Menu</Text>
          <TouchableOpacity onPress={() => setDrawerOpen(false)}>
            <Text style={styles.drawerClose}>✕</Text>
          </TouchableOpacity>
        </View>
        {fullNav.map((navId) => {
          const def = NAV_DEF[navId];
          const active = navId === "home";
          return (
            <TouchableOpacity
              key={navId}
              style={[styles.drawerRow, active && styles.drawerRowActive]}
              onPress={() => goTo(navId)}
            >
              <Text style={styles.drawerIcon}>{def.icon}</Text>
              <Text style={[styles.drawerLabel, active && styles.drawerLabelActive]}>{def.label}</Text>
            </TouchableOpacity>
          );
        })}
      </Animated.View>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: "#0b0f1a" },
  topBar: {
    paddingTop: 58,
    paddingHorizontal: 20,
    paddingBottom: 6,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  brandRow: { flexDirection: "row", alignItems: "center", gap: 9 },
  brandMark: { width: 26, height: 26, borderRadius: 8 },
  workspaceLabel: { fontSize: 14, fontWeight: "700", color: "#f1f5f9" },
  topBarRight: { flexDirection: "row", alignItems: "center", gap: 10 },
  iconButton: {
    width: 36, height: 36, borderRadius: 10, backgroundColor: "#111827",
    borderWidth: 1, borderColor: "rgba(255,255,255,0.08)",
    alignItems: "center", justifyContent: "center",
  },
  iconButtonText: { fontSize: 15 },
  badge: {
    position: "absolute", top: -4, right: -4, minWidth: 16, height: 16, paddingHorizontal: 3,
    borderRadius: 8, backgroundColor: TEAL, alignItems: "center", justifyContent: "center",
  },
  badgeText: { fontSize: 9.5, fontWeight: "800", color: "#0b0f1a" },
  avatar: {
    width: 36, height: 36, borderRadius: 10, backgroundColor: "#1e293b",
    alignItems: "center", justifyContent: "center",
  },
  avatarText: { fontSize: 12, fontWeight: "700", color: "#e2e8f0" },
  body: { flex: 1 },
  bodyContent: { padding: 20, paddingBottom: 110 },
  greeting: { fontSize: 22, fontWeight: "800", color: "#f1f5f9", marginTop: 8 },
  greetingSub: { fontSize: 13.5, color: "#94a3b8", marginTop: 3, marginBottom: 12, lineHeight: 19 },
  error: { color: "#f87185", fontSize: 13, marginBottom: 8 },
  statsRow: { flexDirection: "row", gap: 12, marginBottom: 8 },
  statChip: {
    flex: 1, backgroundColor: "#111827", borderRadius: 14, padding: 14,
    borderWidth: 1, borderColor: "rgba(255,255,255,0.08)",
  },
  statNumber: { fontSize: 22, fontWeight: "800", color: "#f1f5f9" },
  statLabel: { fontSize: 11.5, color: "#94a3b8", marginTop: 2 },
  tileGrid: { flexDirection: "row", flexWrap: "wrap", gap: 12, marginTop: 12 },
  tile: {
    width: "47%", minHeight: 126, borderRadius: 16, padding: 16,
    backgroundColor: "#111827", borderWidth: 1, borderColor: "rgba(255,255,255,0.08)",
    justifyContent: "flex-end", gap: 8,
  },
  tileDisabled: { opacity: 0.6 },
  tileIcon: { width: 42, height: 42, borderRadius: 12, alignItems: "center", justifyContent: "center" },
  tileTitle: { fontSize: 14.5, fontWeight: "700", color: "#f1f5f9" },
  tileSub: { fontSize: 11.5, color: "#94a3b8", lineHeight: 15 },
  tileComingSoon: { fontSize: 10, color: "#eab308", fontWeight: "700" },
  activityCard: {
    marginTop: 22, backgroundColor: "#111827", borderWidth: 1, borderColor: "rgba(255,255,255,0.08)",
    borderRadius: 16, padding: 18,
  },
  activityTitle: { fontSize: 14, fontWeight: "700", color: "#f1f5f9", marginBottom: 8 },
  activityPlaceholder: { fontSize: 12, color: "#64748b", lineHeight: 18 },
  tabBar: {
    position: "absolute", left: 0, right: 0, bottom: 0, flexDirection: "row",
    backgroundColor: "rgba(11,15,26,0.97)", borderTopWidth: 1, borderColor: "rgba(255,255,255,0.08)",
    paddingBottom: 20, paddingTop: 10,
  },
  tabItem: { flex: 1, alignItems: "center", justifyContent: "center", gap: 3 },
  tabIcon: { fontSize: 18 },
  tabLabel: { fontSize: 9.5, fontWeight: "600" },
  backdrop: { ...StyleSheet.absoluteFillObject, backgroundColor: "rgba(0,0,0,0.5)", zIndex: 30 },
  drawer: {
    position: "absolute", top: 0, left: 0, bottom: 0, width: 250, zIndex: 40,
    backgroundColor: "#131c2e", borderRightWidth: 1, borderColor: "rgba(255,255,255,0.08)",
  },
  drawerHeader: {
    flexDirection: "row", alignItems: "center", justifyContent: "space-between",
    paddingTop: 58, paddingHorizontal: 18, paddingBottom: 14,
    borderBottomWidth: 1, borderColor: "rgba(255,255,255,0.08)", marginBottom: 8,
  },
  drawerTitle: { fontSize: 15, fontWeight: "700", color: "#f1f5f9" },
  drawerClose: { fontSize: 14, color: "#94a3b8" },
  drawerRow: {
    flexDirection: "row", alignItems: "center", gap: 12, paddingVertical: 11, paddingHorizontal: 20,
    borderRadius: 10, marginHorizontal: 10,
  },
  drawerRowActive: { backgroundColor: "rgba(45,212,191,0.12)" },
  drawerIcon: { fontSize: 17, width: 24, textAlign: "center" },
  drawerLabel: { fontSize: 13, fontWeight: "600", color: "#cbd5e1" },
  drawerLabelActive: { color: TEAL },
});
