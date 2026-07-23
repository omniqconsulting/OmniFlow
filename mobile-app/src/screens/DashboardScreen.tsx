import { useCallback, useEffect, useMemo, useState } from "react";
import { ActivityIndicator, ScrollView, StyleSheet, Text, TouchableOpacity, View } from "react-native";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";

import type { AuthStackParamList } from "../navigation/AuthNavigator";
import { ApiError } from "../api/client";
import {
  getDashboardFilterOptions,
  getDashboardSummary,
  type DashboardRange,
  type DashboardSummary,
  type FilterOptions,
} from "../api/dashboard";
import BottomSheet from "../components/BottomSheet";

const TEAL = "#2DD4BF";
const INDIGO = "#6657F2";
const RED = "#ef4444";
const AMBER = "#f59e0b";
const GREEN = "#22c55e";
const BLUE = "#3b82f6";
const PURPLE = "#8b5cf6";

type Props = NativeStackScreenProps<AuthStackParamList, "Dashboard">;

const RANGE_DEFS: { key: DashboardRange; label: string }[] = [
  { key: "today", label: "Today" },
  { key: "7d", label: "7 days" },
  { key: "30d", label: "30 days" },
  { key: "mtd", label: "MTD" },
];

function tierColor(pct: number): string {
  return pct >= 80 ? GREEN : pct >= 60 ? AMBER : RED;
}

function toggleInArray<T>(arr: T[], val: T): T[] {
  return arr.includes(val) ? arr.filter((v) => v !== val) : [...arr, val];
}

// Pure-RN circular progress ring (no react-native-svg dependency) — two
// half-circle clip wrappers each holding a full-size rotated disc, the
// standard conic-progress-without-conic-gradient technique.
function ScoreRing({ pct, color, size = 76 }: { pct: number; color: string; size?: number }) {
  const clamped = Math.max(0, Math.min(100, pct));
  const deg = (clamped / 100) * 360;
  const rightDeg = clamped <= 50 ? deg : 180;
  const leftDeg = clamped <= 50 ? 0 : deg - 180;
  const inner = size - 16;
  return (
    <View style={{ width: size, height: size, borderRadius: size / 2, backgroundColor: "rgba(255,255,255,0.08)", overflow: "hidden" }}>
      <View style={{ position: "absolute", width: size / 2, height: size, right: 0, overflow: "hidden" }}>
        <View
          style={{
            position: "absolute", left: -size / 2, width: size, height: size, borderRadius: size / 2,
            backgroundColor: color, transform: [{ rotate: `${rightDeg}deg` }],
          }}
        />
      </View>
      <View style={{ position: "absolute", width: size / 2, height: size, left: 0, overflow: "hidden" }}>
        <View
          style={{
            position: "absolute", right: -size / 2, width: size, height: size, borderRadius: size / 2,
            backgroundColor: color, opacity: clamped > 50 ? 1 : 0, transform: [{ rotate: `${leftDeg}deg` }],
          }}
        />
      </View>
      <View
        style={{
          position: "absolute", top: (size - inner) / 2, left: (size - inner) / 2, width: inner, height: inner,
          borderRadius: inner / 2, backgroundColor: "#111827", alignItems: "center", justifyContent: "center",
        }}
      >
        <Text style={{ fontSize: 15, fontWeight: "800", color }}>{clamped}%</Text>
      </View>
    </View>
  );
}

function scoreLabelFor(score: number): string {
  return score >= 80 ? "Excellent" : score >= 60 ? "Good" : "Needs Attention";
}

export default function DashboardScreen({ navigation, route }: Props) {
  const { user } = route.params;
  const canManage = user.role !== "EMPLOYEE";
  const isAdmin = user.role === "ADMIN";

  const [range, setRange] = useState<DashboardRange>("7d");
  const [filterOpen, setFilterOpen] = useState(false);
  const [filterBranch, setFilterBranch] = useState<string | null>(null);
  const [filterDept, setFilterDept] = useState<string[]>([]);
  const [filterManager, setFilterManager] = useState<string[]>([]);

  const [options, setOptions] = useState<FilterOptions | null>(null);
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!canManage) return;
    getDashboardFilterOptions().then(setOptions).catch(() => {});
  }, [canManage]);

  const load = useCallback(async () => {
    if (!canManage) {
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const s = await getDashboardSummary({
        range,
        branchIds: filterBranch ? [filterBranch] : [],
        deptIds: filterDept,
        managerIds: filterManager,
      });
      setSummary(s);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Couldn't load the dashboard.");
    } finally {
      setLoading(false);
    }
  }, [canManage, range, filterBranch, filterDept, filterManager]);

  useEffect(() => {
    load();
  }, [load]);

  const activeFilterCount = (filterBranch ? 1 : 0) + filterDept.length + filterManager.length;
  const scopeLabel = useMemo(() => {
    if (!options) return "All locations";
    const parts: string[] = [];
    if (filterBranch) {
      const b = options.branches.find((x) => x.id === filterBranch);
      if (b) parts.push(b.name);
    }
    filterDept.forEach((id) => {
      const d = options.departments.find((x) => x.id === id);
      if (d) parts.push(d.name);
    });
    filterManager.forEach((id) => {
      const m = options.managers.find((x) => x.id === id);
      if (m) parts.push(m.name);
    });
    return parts.length ? parts.join(" · ") : "All locations";
  }, [options, filterBranch, filterDept, filterManager]);

  const chipPair = (active: boolean, color: string) => ({
    box: [styles.chip, active ? { backgroundColor: color + "26", borderColor: color } : styles.chipInactive],
    text: [styles.chipText, { color: active ? color : "#94a3b8" }],
  });

  if (!canManage) {
    return (
      <View style={styles.screen}>
        <Header navigation={navigation} scopeLabel={undefined} canManage={false} />
        <View style={styles.emptyWrap}>
          <Text style={styles.emptyIcon}>📊</Text>
          <Text style={styles.emptyTitle}>Dashboard is for Managers & Admins</Text>
          <Text style={styles.emptySub}>Operations analytics aren't part of the Employee view. Head to Home for your personal daily overview.</Text>
        </View>
      </View>
    );
  }

  return (
    <View style={styles.screen}>
      <Header
        navigation={navigation}
        scopeLabel={scopeLabel}
        canManage
        activeFilterCount={activeFilterCount}
        onOpenFilter={() => setFilterOpen(true)}
      />

      <ScrollView style={styles.body} contentContainerStyle={styles.bodyContent}>
        {error ? <Text style={styles.error}>{error}</Text> : null}
        {loading && !summary ? <ActivityIndicator color={TEAL} style={{ marginTop: 16 }} /> : null}

        <View style={styles.rangeRow}>
          {RANGE_DEFS.map((r) => {
            const active = range === r.key;
            return (
              <TouchableOpacity
                key={r.key}
                style={[styles.rangeChip, active && styles.rangeChipActive]}
                onPress={() => setRange(r.key)}
              >
                <Text style={[styles.rangeChipText, active && styles.rangeChipTextActive]}>{r.label}</Text>
              </TouchableOpacity>
            );
          })}
        </View>

        {summary ? (
          <>
            <View style={styles.scoreRow}>
              <View style={styles.scoreCard}>
                <Text style={styles.scoreCardLabel}>Overall</Text>
                <ScoreRing pct={summary.score} color={tierColor(summary.score)} />
                <Text style={styles.scoreCardSub}>{scoreLabelFor(summary.score)}</Text>
              </View>
              <View style={styles.breakdownCard}>
                {summary.components.map((c) => (
                  <View key={c.label} style={{ marginBottom: 9 }}>
                    <View style={styles.breakdownRow}>
                      <Text style={[styles.breakdownLabel, { color: c.color }]}>{c.label}</Text>
                      <Text style={styles.breakdownValue}>{c.value}%</Text>
                    </View>
                    <View style={styles.breakdownTrack}>
                      <View style={[styles.breakdownFill, { width: `${c.value}%`, backgroundColor: c.color }]} />
                    </View>
                  </View>
                ))}
              </View>
            </View>

            {summary.tickets ? (
              <TouchableOpacity
                style={[styles.statCard, { borderLeftColor: BLUE }]}
                onPress={() => navigation.navigate("Tickets", { user })}
              >
                <View style={styles.statCardHeader}>
                  <Text style={{ fontSize: 15 }}>🎫</Text>
                  <Text style={styles.statCardTitle}>Tickets</Text>
                  <Text style={styles.statCardLink}>Open ›</Text>
                </View>
                <View style={styles.statGrid}>
                  <StatCell label="Total" value={summary.tickets.total} sub={`${summary.tickets.open} open`} />
                  <StatCell label="Completed" value={summary.tickets.completed} valueColor={GREEN} sub={`of ${summary.tickets.total} created`} />
                  <StatCell label="On Time" value={summary.tickets.on_time_count} valueColor={tierColor(summary.tickets.on_time_pct)} sub={`${summary.tickets.on_time_pct}% of closed`} />
                  <StatCell label="Issues Raised" value={summary.tickets.issues_open} valueColor={summary.tickets.issues_open > 0 ? RED : GREEN} sub="help tickets open" />
                </View>
              </TouchableOpacity>
            ) : null}

            {summary.checklists ? (
              <TouchableOpacity
                style={[styles.statCard, { borderLeftColor: TEAL }]}
                onPress={() => navigation.navigate("Checklists", { user })}
              >
                <View style={styles.statCardHeader}>
                  <Text style={{ fontSize: 15 }}>✅</Text>
                  <Text style={styles.statCardTitle}>Checklists</Text>
                  <Text style={styles.statCardLink}>Open ›</Text>
                </View>
                <View style={styles.statGrid}>
                  <StatCell label="Due" value={summary.checklists.due} sub="in period" />
                  <StatCell label="Completed" value={summary.checklists.completed} valueColor={tierColor(summary.checklists.compliance_pct)} sub={`${summary.checklists.compliance_pct}% compliance`} />
                  <StatCell label="On Time" value={summary.checklists.on_time} sub={`of ${summary.checklists.completed} completed`} />
                  <StatCell label="Missed" value={summary.checklists.missed} valueColor={summary.checklists.missed > 0 ? RED : GREEN} sub="not completed" />
                </View>
              </TouchableOpacity>
            ) : null}

            {summary.fms ? (
              <View style={[styles.statCard, { borderLeftColor: PURPLE }]}>
                <View style={styles.statCardHeader}>
                  <Text style={{ fontSize: 15 }}>🔀</Text>
                  <Text style={styles.statCardTitle}>Flow Tickets</Text>
                </View>
                <View style={styles.statGrid}>
                  <StatCell label="Total" value={summary.fms.total} sub={`${summary.fms.active} active · ${summary.fms.completed} done`} />
                  <StatCell label="Completed" value={summary.fms.completed} valueColor={GREEN} sub="in period" />
                  <StatCell label="On Time" value={summary.fms.on_time} sub={`of ${summary.fms.completed} completed`} />
                  <StatCell label="TaT Breach" value={summary.fms.tat_breach} valueColor={summary.fms.tat_breach > 0 ? RED : GREEN} sub="stage TaT exceeded" />
                </View>
              </View>
            ) : null}

            <View style={styles.priorityCard}>
              <View style={styles.priorityHeader}>
                <Text style={{ fontSize: 14 }}>🔥</Text>
                <Text style={styles.priorityTitle}>Priority Tasks</Text>
                <Text style={styles.priorityBadge}>{summary.priority_tasks.length}</Text>
              </View>
              {summary.priority_tasks.map((p) => (
                <View key={p.id} style={styles.priorityRow}>
                  <Text style={styles.priorityItemTitle}>{p.title}</Text>
                  <Text style={styles.priorityItemMeta}>
                    {p.assignee_name ?? "Unassigned"} ·{" "}
                    <Text style={{ color: p.overdue ? RED : "#94a3b8" }}>
                      {p.due_at ? new Date(p.due_at).toLocaleDateString("en-IN", { day: "numeric", month: "short" }) : "No due date"}
                    </Text>
                  </Text>
                </View>
              ))}
              {summary.priority_tasks.length === 0 ? <Text style={styles.priorityEmpty}>No critical open tickets.</Text> : null}
            </View>

            {summary.dept_health.length > 0 ? (
              <>
                <Text style={styles.sectionLabel}>Department Health</Text>
                <View style={styles.deptRow}>
                  {summary.dept_health.map((d) => {
                    const color = tierColor(d.rate);
                    return (
                      <View key={d.dept_id} style={[styles.deptChip, { backgroundColor: color + "22" }]}>
                        <Text style={[styles.deptChipText, { color }]}>
                          {d.name} <Text style={{ opacity: 0.7, fontSize: 10 }}>{d.rate}%</Text>
                        </Text>
                      </View>
                    );
                  })}
                </View>
              </>
            ) : null}
          </>
        ) : null}
      </ScrollView>

      <BottomSheet visible={filterOpen} onClose={() => setFilterOpen(false)}>
        <Text style={styles.sheetTitle}>Filter Dashboard</Text>

        <Text style={styles.sheetLabel}>Branch / Location</Text>
        <View style={styles.chipWrap}>
          <TouchableOpacity
            style={chipPair(filterBranch === null, BLUE).box as any}
            onPress={() => setFilterBranch(null)}
          >
            <Text style={chipPair(filterBranch === null, BLUE).text as any}>All Locations</Text>
          </TouchableOpacity>
          {(options?.branches ?? []).map((b) => {
            const active = filterBranch === b.id;
            const p = chipPair(active, BLUE);
            return (
              <TouchableOpacity key={b.id} style={p.box as any} onPress={() => setFilterBranch(b.id)}>
                <Text style={p.text as any}>{b.name}</Text>
              </TouchableOpacity>
            );
          })}
        </View>

        <Text style={styles.sheetLabel}>Department</Text>
        <View style={styles.chipWrap}>
          {(options?.departments ?? []).map((d) => {
            const active = filterDept.includes(d.id);
            const p = chipPair(active, INDIGO);
            return (
              <TouchableOpacity key={d.id} style={p.box as any} onPress={() => setFilterDept((prev) => toggleInArray(prev, d.id))}>
                <Text style={p.text as any}>{d.name}</Text>
              </TouchableOpacity>
            );
          })}
        </View>

        {isAdmin ? (
          <>
            <Text style={styles.sheetLabel}>Manager</Text>
            <View style={styles.chipWrap}>
              {(options?.managers ?? []).map((m) => {
                const active = filterManager.includes(m.id);
                const p = chipPair(active, PURPLE);
                return (
                  <TouchableOpacity key={m.id} style={p.box as any} onPress={() => setFilterManager((prev) => toggleInArray(prev, m.id))}>
                    <Text style={p.text as any}>{m.name}</Text>
                  </TouchableOpacity>
                );
              })}
            </View>
          </>
        ) : null}

        <View style={styles.sheetActions}>
          <TouchableOpacity
            style={styles.clearButton}
            onPress={() => {
              setFilterBranch(null);
              setFilterDept([]);
              setFilterManager([]);
            }}
          >
            <Text style={styles.clearButtonText}>Clear</Text>
          </TouchableOpacity>
          <TouchableOpacity style={styles.applyButton} onPress={() => setFilterOpen(false)}>
            <Text style={styles.applyButtonText}>Apply</Text>
          </TouchableOpacity>
        </View>
      </BottomSheet>
    </View>
  );
}

function StatCell({ label, value, sub, valueColor }: { label: string; value: number; sub: string; valueColor?: string }) {
  return (
    <View>
      <Text style={styles.statCellLabel}>{label}</Text>
      <Text style={[styles.statCellValue, valueColor ? { color: valueColor } : null]}>{value}</Text>
      <Text style={styles.statCellSub}>{sub}</Text>
    </View>
  );
}

function Header({
  navigation, scopeLabel, canManage, activeFilterCount, onOpenFilter,
}: {
  navigation: Props["navigation"];
  scopeLabel: string | undefined;
  canManage: boolean;
  activeFilterCount?: number;
  onOpenFilter?: () => void;
}) {
  return (
    <View style={styles.topBar}>
      <View style={styles.topBarLeft}>
        <TouchableOpacity style={styles.backButton} onPress={() => navigation.goBack()}>
          <Text style={styles.backIcon}>‹</Text>
        </TouchableOpacity>
        <View>
          <Text style={styles.title}>Dashboard</Text>
          {scopeLabel ? <Text style={styles.subtitle}>{scopeLabel}</Text> : null}
        </View>
      </View>
      {canManage && onOpenFilter ? (
        <TouchableOpacity style={styles.filterButton} onPress={onOpenFilter}>
          <Text style={styles.filterIcon}>▤</Text>
          {activeFilterCount ? (
            <View style={styles.filterBadge}>
              <Text style={styles.filterBadgeText}>{activeFilterCount}</Text>
            </View>
          ) : null}
        </TouchableOpacity>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: "#0b0f1a" },
  topBar: { paddingTop: 54, paddingHorizontal: 20, paddingBottom: 10, flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  topBarLeft: { flexDirection: "row", alignItems: "center", gap: 12 },
  backButton: { width: 34, height: 34, borderRadius: 10, backgroundColor: "#111827", borderWidth: 1, borderColor: "rgba(255,255,255,0.08)", alignItems: "center", justifyContent: "center" },
  backIcon: { fontSize: 16, color: "#cbd5e1" },
  title: { fontSize: 17.5, fontWeight: "800", color: "#f1f5f9" },
  subtitle: { fontSize: 11, color: "#64748b", marginTop: 1 },
  filterButton: { width: 34, height: 34, borderRadius: 10, backgroundColor: "#111827", borderWidth: 1, borderColor: "rgba(255,255,255,0.08)", alignItems: "center", justifyContent: "center" },
  filterIcon: { fontSize: 15, color: "#cbd5e1" },
  filterBadge: { position: "absolute", top: -4, right: -4, minWidth: 15, height: 15, paddingHorizontal: 3, borderRadius: 8, backgroundColor: TEAL, alignItems: "center", justifyContent: "center" },
  filterBadgeText: { fontSize: 9, fontWeight: "800", color: "#0b0f1a" },
  body: { flex: 1 },
  bodyContent: { padding: 20, paddingBottom: 40 },
  error: { color: "#f87185", fontSize: 13, marginBottom: 8 },
  emptyWrap: { flex: 1, alignItems: "center", justifyContent: "center", paddingHorizontal: 40 },
  emptyIcon: { fontSize: 32, marginBottom: 12 },
  emptyTitle: { fontSize: 14.5, fontWeight: "700", color: "#f1f5f9", marginBottom: 6, textAlign: "center" },
  emptySub: { fontSize: 12.5, color: "#94a3b8", lineHeight: 18, textAlign: "center" },
  rangeRow: { flexDirection: "row", gap: 6, paddingVertical: 6, marginBottom: 10 },
  rangeChip: { flex: 1, height: 38, borderRadius: 11, alignItems: "center", justifyContent: "center", backgroundColor: "#111827", borderWidth: 1, borderColor: "rgba(255,255,255,0.08)" },
  rangeChipActive: { backgroundColor: "rgba(45,212,191,0.14)", borderColor: "rgba(45,212,191,0.35)" },
  rangeChipText: { fontSize: 12, fontWeight: "700", color: "#94a3b8" },
  rangeChipTextActive: { color: TEAL },
  scoreRow: { flexDirection: "row", gap: 12, marginBottom: 16 },
  scoreCard: { width: 112, backgroundColor: "#111827", borderWidth: 1, borderColor: "rgba(255,255,255,0.08)", borderRadius: 14, paddingVertical: 14, paddingHorizontal: 10, alignItems: "center", justifyContent: "center", gap: 8 },
  scoreCardLabel: { fontSize: 9.5, fontWeight: "700", color: "#64748b", textTransform: "uppercase", letterSpacing: 0.5 },
  scoreCardSub: { fontSize: 10, color: "#64748b" },
  breakdownCard: { flex: 1, backgroundColor: "#111827", borderWidth: 1, borderColor: "rgba(255,255,255,0.08)", borderRadius: 14, padding: 14, justifyContent: "center" },
  breakdownRow: { flexDirection: "row", justifyContent: "space-between", marginBottom: 4 },
  breakdownLabel: { fontSize: 11.5, fontWeight: "600" },
  breakdownValue: { fontSize: 11.5, fontWeight: "700", color: "#e2e8f0" },
  breakdownTrack: { height: 5, borderRadius: 3, backgroundColor: "rgba(255,255,255,0.08)", overflow: "hidden" },
  breakdownFill: { height: "100%", borderRadius: 3 },
  statCard: { backgroundColor: "#111827", borderWidth: 1, borderColor: "rgba(255,255,255,0.08)", borderLeftWidth: 3, borderRadius: 14, borderTopLeftRadius: 0, borderBottomLeftRadius: 0, padding: 14, marginBottom: 12 },
  statCardHeader: { flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 12 },
  statCardTitle: { fontSize: 13.5, fontWeight: "700", color: "#f1f5f9" },
  statCardLink: { marginLeft: "auto", fontSize: 11.5, fontWeight: "700", color: TEAL },
  statGrid: { flexDirection: "row", flexWrap: "wrap", gap: 12 },
  statCellLabel: { fontSize: 9.5, fontWeight: "700", color: "#64748b", textTransform: "uppercase", letterSpacing: 0.4, marginBottom: 4, width: 130 },
  statCellValue: { fontSize: 20, fontWeight: "800", color: "#e2e8f0" },
  statCellSub: { fontSize: 10.5, color: "#64748b", marginTop: 2 },
  priorityCard: { backgroundColor: "rgba(239,68,68,0.06)", borderWidth: 1, borderColor: "rgba(239,68,68,0.2)", borderRadius: 14, padding: 14, marginBottom: 16 },
  priorityHeader: { flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 10 },
  priorityTitle: { fontSize: 13, fontWeight: "700", color: "#f87171" },
  priorityBadge: { marginLeft: "auto", fontSize: 10, fontWeight: "800", color: "#f87171", backgroundColor: "rgba(239,68,68,0.2)", borderRadius: 8, paddingHorizontal: 7, paddingVertical: 2 },
  priorityRow: { paddingVertical: 9, borderTopWidth: 1, borderColor: "rgba(239,68,68,0.12)" },
  priorityItemTitle: { fontSize: 12.5, fontWeight: "700", color: "#e2e8f0" },
  priorityItemMeta: { fontSize: 11, color: "#94a3b8", marginTop: 2 },
  priorityEmpty: { fontSize: 12, color: "#94a3b8", paddingVertical: 6 },
  sectionLabel: { fontSize: 11, fontWeight: "700", textTransform: "uppercase", letterSpacing: 0.5, color: "#64748b", marginBottom: 10 },
  deptRow: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  deptChip: { paddingVertical: 7, paddingHorizontal: 12, borderRadius: 8 },
  deptChipText: { fontSize: 12, fontWeight: "600" },
  sheetTitle: { fontSize: 16, fontWeight: "800", color: "#f1f5f9", marginBottom: 16 },
  sheetLabel: { fontSize: 12, fontWeight: "600", color: "#94a3b8", marginBottom: 7 },
  chipWrap: { flexDirection: "row", gap: 7, flexWrap: "wrap", marginBottom: 16 },
  chip: { paddingVertical: 8, paddingHorizontal: 13, borderRadius: 999, borderWidth: 1 },
  chipInactive: { backgroundColor: "#0d1424", borderColor: "rgba(255,255,255,0.1)" },
  chipText: { fontSize: 12.5, fontWeight: "600" },
  sheetActions: { flexDirection: "row", gap: 10, marginTop: 8 },
  clearButton: { flex: 1, height: 48, borderRadius: 12, borderWidth: 1, borderColor: "rgba(255,255,255,0.12)", alignItems: "center", justifyContent: "center" },
  clearButtonText: { fontSize: 14, fontWeight: "700", color: "#cbd5e1" },
  applyButton: { flex: 2, height: 48, borderRadius: 12, backgroundColor: INDIGO, alignItems: "center", justifyContent: "center" },
  applyButtonText: { fontSize: 15, fontWeight: "700", color: "#fff" },
});
