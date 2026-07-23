import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
import { getHomeActivity, getHomeSummary, type ActivityItem, type HomeSummary } from "../api/home";
import { ApiError } from "../api/client";
import { useTheme, type ThemeColors } from "../theme/ThemeContext";
import {
  BOTTOM_COUNT,
  BUILT_SCREENS,
  CATEGORY_COLOR,
  FULL_NAV,
  GREETING_SUB,
  isNavEnabled,
  NAV_DEF,
  TILES,
  timeOfDayGreeting,
  type NavId,
} from "../config/roleNav";

type Props = NativeStackScreenProps<AuthStackParamList, "Home">;

function nameInitials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

export default function HomeScreen({ navigation, route }: Props) {
  const { user } = route.params;
  const { colors, toggleTheme } = useTheme();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  const [summary, setSummary] = useState<HomeSummary | null>(null);
  const [activity, setActivity] = useState<ActivityItem[]>([]);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const drawerX = useRef(new Animated.Value(-260)).current;
  const refreshSpin = useRef(new Animated.Value(0)).current;

  const load = useCallback(async () => {
    try {
      const [s, a] = await Promise.all([getHomeSummary(), getHomeActivity()]);
      setSummary(s);
      setActivity(a);
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

  useEffect(() => {
    if (!refreshing) {
      refreshSpin.setValue(0);
      return;
    }
    const loop = Animated.loop(
      Animated.timing(refreshSpin, { toValue: 1, duration: 700, useNativeDriver: true })
    );
    loop.start();
    return () => loop.stop();
  }, [refreshing, refreshSpin]);
  const spinDeg = refreshSpin.interpolate({ inputRange: [0, 1], outputRange: ["0deg", "360deg"] });

  const role = user.role in TILES ? user.role : "EMPLOYEE";
  // Conservative default: until the real Setup-driven list loads, treat
  // everything gated as hidden rather than briefly showing something the
  // tenant's plan/Setup config doesn't actually allow.
  const enabledTabs = summary?.enabled_tabs ?? [];
  const tiles = (TILES[role] ?? []).filter((tile) => isNavEnabled(tile.nav, enabledTabs));
  const fullNav = (FULL_NAV[role] ?? FULL_NAV.EMPLOYEE).filter((navId) => isNavEnabled(navId, enabledTabs));
  const bottomCount = BOTTOM_COUNT[role] ?? 4;
  const bottomTabs = fullNav.slice(0, bottomCount);

  const goTo = (navId: NavId) => {
    setDrawerOpen(false);
    const screen = BUILT_SCREENS[navId];
    if (!screen) {
      Alert.alert("Coming soon", `${NAV_DEF[navId].label} isn't built in the app yet.`);
      return;
    }
    const navigators: Partial<Record<keyof AuthStackParamList, () => void>> = {
      Attendance: () => navigation.navigate("Attendance", { user }),
      Tickets: () => navigation.navigate("Tickets", { user }),
      Setup: () => navigation.navigate("Setup", { user }),
      Dashboard: () => navigation.navigate("Dashboard", { user }),
      Checklists: () => navigation.navigate("Checklists", { user }),
      FMSFlowBoard: () => navigation.navigate("FMSFlowBoard", { user }),
      MyTasks: () => navigation.navigate("MyTasks", { user }),
      Organization: () => navigation.navigate("Organization", { user }),
      OrgTraining: () => navigation.navigate("OrgTraining", { user }),
    };
    navigators[screen]?.(); // no-op for "Home" (already here)
  };

  return (
    <View style={styles.screen}>
      <View style={styles.topBar}>
        <View style={styles.brandRow}>
          <LinearGradient colors={[colors.teal, colors.indigo]} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={styles.brandMark} />
          <Text style={styles.workspaceLabel}>OmniFlow</Text>
        </View>
        <View style={styles.topBarRight}>
          <TouchableOpacity style={styles.iconButton} onPress={toggleTheme}>
            <Text style={styles.iconButtonText}>{colors.mode === "dark" ? "☀️" : "🌙"}</Text>
          </TouchableOpacity>
          <TouchableOpacity style={styles.iconButton} onPress={onRefresh} disabled={refreshing}>
            <Animated.Text style={[styles.refreshIcon, { transform: [{ rotate: spinDeg }] }]}>↻</Animated.Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={styles.iconButton}
            onPress={() => navigation.navigate("Notifications", { user })}
          >
            <Text style={styles.iconButtonText}>🔔</Text>
            {summary && summary.unread_notifications > 0 ? (
              <View style={styles.badge}>
                <Text style={styles.badgeText}>{summary.unread_notifications}</Text>
              </View>
            ) : null}
          </TouchableOpacity>
          <TouchableOpacity
            style={styles.avatar}
            onPress={() => navigation.navigate("Profile", { user, slug: route.params.slug })}
          >
            <Text style={styles.avatarText}>{nameInitials(user.name)}</Text>
          </TouchableOpacity>
        </View>
      </View>

      <ScrollView
        style={styles.body}
        contentContainerStyle={styles.bodyContent}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.teal} />}
      >
        <Text style={styles.greeting}>{timeOfDayGreeting()}, {user.name}</Text>
        <Text style={styles.greetingSub}>{GREETING_SUB[role]}</Text>

        {error ? <Text style={styles.error}>{error}</Text> : null}
        {!summary && !error ? <ActivityIndicator color={colors.teal} style={{ marginTop: 16 }} /> : null}

        <View style={styles.tileGrid}>
          {tiles.map((tile) => {
            const tcolors = CATEGORY_COLOR[tile.cat];
            const built = !!BUILT_SCREENS[tile.nav];
            return (
              <TouchableOpacity
                key={tile.title}
                style={[styles.tile, !built && styles.tileDisabled]}
                onPress={() => goTo(tile.nav)}
              >
                <View style={[styles.tileIcon, { backgroundColor: tcolors.bg }]}>
                  <Text style={{ fontSize: 19 }}>{tile.icon}</Text>
                </View>
                <Text style={styles.tileTitle}>{tile.title}</Text>
                <Text style={styles.tileSub}>{tile.sub}</Text>
                {!built ? <Text style={styles.tileComingSoon}>Coming soon</Text> : null}
              </TouchableOpacity>
            );
          })}
        </View>

        {activity.length > 0 ? (
          <View style={styles.activityCard}>
            <View style={styles.activityHeader}>
              <Text style={styles.activityTitle}>Recent activity</Text>
              <TouchableOpacity onPress={() => navigation.navigate("Tickets", { user })}>
                <Text style={styles.activityViewAll}>View all</Text>
              </TouchableOpacity>
            </View>
            {activity.map((item, idx) => {
              const acolors = CATEGORY_COLOR[(item.cat as keyof typeof CATEGORY_COLOR) ?? "op"];
              return (
                <View key={idx} style={[styles.activityRow, idx === 0 && styles.activityRowFirst]}>
                  <View style={[styles.activityIcon, { backgroundColor: acolors.bg }]}>
                    <Text style={{ fontSize: 14 }}>{item.icon}</Text>
                  </View>
                  <View style={styles.activityBody}>
                    <Text style={styles.activityItemTitle} numberOfLines={1}>{item.title}</Text>
                    <Text style={styles.activityItemMeta}>{item.meta}</Text>
                  </View>
                  <Text style={styles.activityItemRel}>{item.rel}</Text>
                </View>
              );
            })}
          </View>
        ) : null}
      </ScrollView>

      <View style={styles.tabBar}>
        {bottomTabs.map((navId) => {
          const def = NAV_DEF[navId];
          const active = !drawerOpen && navId === "home";
          const color = active ? colors.teal : colors.textMuted;
          return (
            <TouchableOpacity key={navId} style={styles.tabItem} onPress={() => goTo(navId)}>
              <Text style={[styles.tabIcon, { color }]}>{def.icon}</Text>
              <Text style={[styles.tabLabel, { color }]}>{def.label}</Text>
            </TouchableOpacity>
          );
        })}
        <TouchableOpacity style={styles.tabItem} onPress={() => setDrawerOpen((v) => !v)}>
          <Text style={[styles.tabIcon, { color: drawerOpen ? colors.teal : colors.textMuted }]}>☰</Text>
          <Text style={[styles.tabLabel, { color: drawerOpen ? colors.teal : colors.textMuted }]}>Menu</Text>
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

function makeStyles(colors: ThemeColors) {
  return StyleSheet.create({
    screen: { flex: 1, backgroundColor: colors.screenBg },
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
    workspaceLabel: { fontSize: 15, fontWeight: "800", color: colors.textPrimary, letterSpacing: 0.2 },
    topBarRight: { flexDirection: "row", alignItems: "center", gap: 10 },
    iconButton: {
      width: 36, height: 36, borderRadius: 10, backgroundColor: colors.iconButtonBg,
      borderWidth: 1, borderColor: colors.border,
      alignItems: "center", justifyContent: "center",
    },
    iconButtonText: { fontSize: 15 },
    refreshIcon: { fontSize: 19, fontWeight: "700", color: colors.textSecondary },
    badge: {
      position: "absolute", top: -4, right: -4, minWidth: 16, height: 16, paddingHorizontal: 3,
      borderRadius: 8, backgroundColor: colors.teal, alignItems: "center", justifyContent: "center",
    },
    badgeText: { fontSize: 9.5, fontWeight: "800", color: "#0b0f1a" },
    avatar: {
      width: 36, height: 36, borderRadius: 10, backgroundColor: colors.cardBg,
      borderWidth: 1, borderColor: colors.border,
      alignItems: "center", justifyContent: "center",
    },
    avatarText: { fontSize: 12, fontWeight: "700", color: colors.textPrimary },
    body: { flex: 1 },
    bodyContent: { padding: 20, paddingBottom: 110 },
    greeting: { fontSize: 22, fontWeight: "800", color: colors.textPrimary, marginTop: 8 },
    greetingSub: { fontSize: 13.5, color: colors.textSecondary, marginTop: 3, marginBottom: 12, lineHeight: 19 },
    error: { color: "#f87185", fontSize: 13, marginBottom: 8 },
    tileGrid: { flexDirection: "row", flexWrap: "wrap", gap: 12, marginTop: 4 },
    tile: {
      width: "47%", minHeight: 126, borderRadius: 16, padding: 16,
      backgroundColor: colors.cardBg, borderWidth: 1, borderColor: colors.border,
      justifyContent: "flex-end", gap: 8,
    },
    tileDisabled: { opacity: 0.6 },
    tileIcon: { width: 42, height: 42, borderRadius: 12, alignItems: "center", justifyContent: "center" },
    tileTitle: { fontSize: 14.5, fontWeight: "700", color: colors.textPrimary },
    tileSub: { fontSize: 11.5, color: colors.textSecondary, lineHeight: 15 },
    tileComingSoon: { fontSize: 10, color: "#eab308", fontWeight: "700" },
    activityCard: {
      marginTop: 22, backgroundColor: colors.cardBg, borderWidth: 1, borderColor: colors.border,
      borderRadius: 16, padding: 18,
    },
    activityHeader: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
    activityTitle: { fontSize: 14, fontWeight: "700", color: colors.textPrimary },
    activityViewAll: { fontSize: 12, fontWeight: "600", color: colors.teal },
    activityRow: {
      flexDirection: "row", alignItems: "center", gap: 11, paddingVertical: 12,
      borderTopWidth: 1, borderColor: colors.border, marginTop: 4,
    },
    activityRowFirst: { borderTopWidth: 0, marginTop: 12 },
    activityIcon: { width: 32, height: 32, borderRadius: 9, alignItems: "center", justifyContent: "center", flexShrink: 0 },
    activityBody: { flex: 1, minWidth: 0 },
    activityItemTitle: { fontSize: 12.5, fontWeight: "600", color: colors.textPrimary },
    activityItemMeta: { fontSize: 11, color: colors.textSecondary, marginTop: 1 },
    activityItemRel: { fontSize: 10.5, color: colors.textMuted, flexShrink: 0 },
    tabBar: {
      position: "absolute", left: 0, right: 0, bottom: 0, flexDirection: "row",
      backgroundColor: colors.tabBarBg, borderTopWidth: 1, borderColor: colors.border,
      paddingBottom: 20, paddingTop: 10,
    },
    tabItem: { flex: 1, alignItems: "center", justifyContent: "center", gap: 3 },
    tabIcon: { fontSize: 18 },
    tabLabel: { fontSize: 9.5, fontWeight: "600" },
    backdrop: { ...StyleSheet.absoluteFillObject, backgroundColor: "rgba(0,0,0,0.5)", zIndex: 30 },
    drawer: {
      position: "absolute", top: 0, left: 0, bottom: 0, width: 250, zIndex: 40,
      backgroundColor: colors.cardBg, borderRightWidth: 1, borderColor: colors.border,
    },
    drawerHeader: {
      flexDirection: "row", alignItems: "center", justifyContent: "space-between",
      paddingTop: 58, paddingHorizontal: 18, paddingBottom: 14,
      borderBottomWidth: 1, borderColor: colors.border, marginBottom: 8,
    },
    drawerTitle: { fontSize: 15, fontWeight: "700", color: colors.textPrimary },
    drawerClose: { fontSize: 14, color: colors.textSecondary },
    drawerRow: {
      flexDirection: "row", alignItems: "center", gap: 12, paddingVertical: 11, paddingHorizontal: 20,
      borderRadius: 10, marginHorizontal: 10,
    },
    drawerRowActive: { backgroundColor: "rgba(45,212,191,0.12)" },
    drawerIcon: { fontSize: 17, width: 24, textAlign: "center" },
    drawerLabel: { fontSize: 13, fontWeight: "600", color: colors.textSecondary },
    drawerLabelActive: { color: colors.teal },
  });
}
