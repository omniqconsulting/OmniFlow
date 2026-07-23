import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Image, Linking, Modal, ScrollView, StyleSheet, Text, TouchableOpacity, View } from "react-native";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";

import type { AuthStackParamList } from "../navigation/AuthNavigator";
import { ApiError, API_BASE_URL } from "../api/client";
import { listKnowledge, type KnowledgeItem, type KnowledgeMediaKind } from "../api/training";
import { useTheme, type ThemeColors } from "../theme/ThemeContext";

type Props = NativeStackScreenProps<AuthStackParamList, "OrgTraining">;

const KIND_META: Record<KnowledgeMediaKind, { icon: string; label: string; bg: string; fg: string }> = {
  document: { icon: "📄", label: "Document", bg: "rgba(148,163,184,0.14)", fg: "#cbd5e1" },
  video: { icon: "🎬", label: "Video", bg: "rgba(239,68,68,0.14)", fg: "#f87171" },
  audio: { icon: "🎵", label: "Audio", bg: "rgba(245,158,11,0.14)", fg: "#f59e0b" },
  image: { icon: "🖼", label: "Image", bg: "rgba(34,197,94,0.14)", fg: "#22c55e" },
  link: { icon: "🔗", label: "Link", bg: "rgba(148,163,184,0.14)", fg: "#cbd5e1" },
};

const KIND_TABS: { value: KnowledgeMediaKind | ""; label: string }[] = [
  { value: "", label: "All" },
  { value: "document", label: "Docs" },
  { value: "video", label: "Video" },
  { value: "audio", label: "Audio" },
  { value: "image", label: "Image" },
  { value: "link", label: "Links" },
];

function resolveUrl(url: string | null): string | null {
  if (!url) return null;
  if (/^https?:\/\//i.test(url)) return url;
  return `${API_BASE_URL}${url}`;
}

export default function OrgTrainingScreen({ navigation }: Props) {
  const { colors } = useTheme();
  const styles = makeStyles(colors);
  const [items, setItems] = useState<KnowledgeItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [kind, setKind] = useState<KnowledgeMediaKind | "">("");
  const [category, setCategory] = useState<string>("");
  const [filterOpen, setFilterOpen] = useState(false);
  const [viewer, setViewer] = useState<KnowledgeItem | null>(null);

  const load = useCallback(async () => {
    try {
      const page = await listKnowledge({ category: category || undefined });
      setItems(page.items);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Couldn't load training materials.");
    }
  }, [category]);

  useEffect(() => {
    load();
  }, [load]);

  const categories = Array.from(new Set((items ?? []).map((i) => i.category).filter((c): c is string => !!c)));
  const visible = (items ?? []).filter((i) => !kind || i.media_kind === kind);

  const openItem = (item: KnowledgeItem) => {
    if (item.external_url && !item.file_url && item.media_kind !== "image") {
      Linking.openURL(item.external_url).catch(() => {});
      return;
    }
    setViewer(item);
  };

  return (
    <View style={styles.screen}>
      <View style={styles.topBar}>
        <TouchableOpacity style={styles.backButton} onPress={() => navigation.goBack()}>
          <Text style={styles.backIcon}>‹</Text>
        </TouchableOpacity>
        <View style={{ flex: 1 }}>
          <Text style={styles.title}>Training</Text>
          <Text style={styles.subtitle}>Knowledge & materials</Text>
        </View>
        <TouchableOpacity style={styles.filterButton} onPress={() => setFilterOpen(true)}>
          <Text style={{ fontSize: 14, color: colors.textSecondary }}>▤</Text>
          {category ? <View style={styles.filterDot} /> : null}
        </TouchableOpacity>
      </View>

      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.kindRow} contentContainerStyle={{ paddingHorizontal: 20, gap: 6 }}>
        {KIND_TABS.map((t) => {
          const count = t.value ? (items ?? []).filter((i) => i.media_kind === t.value).length : (items ?? []).length;
          const active = kind === t.value;
          return (
            <TouchableOpacity key={t.value || "all"} style={[styles.chip, active && styles.chipActive]} onPress={() => setKind(t.value)}>
              <Text style={[styles.chipText, active && styles.chipTextActive]}>{t.label} {count}</Text>
            </TouchableOpacity>
          );
        })}
      </ScrollView>

      {!items && !error ? <ActivityIndicator color={colors.teal} style={{ marginTop: 24 }} /> : null}
      {error ? <Text style={styles.error}>{error}</Text> : null}

      <ScrollView style={styles.body} contentContainerStyle={styles.bodyContent}>
        {items && visible.length === 0 ? (
          <Text style={styles.emptyText}>No training material matches this filter.</Text>
        ) : null}
        {visible.map((item) => {
          const meta = KIND_META[item.media_kind];
          return (
            <TouchableOpacity key={item.id} style={styles.card} onPress={() => openItem(item)}>
              <View style={{ flexDirection: "row", gap: 10, alignItems: "flex-start" }}>
                <Text style={{ fontSize: 20 }}>{meta.icon}</Text>
                <View style={{ flex: 1, minWidth: 0 }}>
                  <Text style={styles.cardTitle}>{item.title}</Text>
                  <Text style={styles.cardMeta}>{item.category || "Uncategorized"}</Text>
                </View>
              </View>
              <View style={styles.cardFooter}>
                <View style={[styles.badge, { backgroundColor: meta.bg }]}>
                  <Text style={[styles.badgeText, { color: meta.fg }]}>{meta.label}</Text>
                </View>
              </View>
            </TouchableOpacity>
          );
        })}
      </ScrollView>

      {/* Category filter sheet */}
      <Modal visible={filterOpen} transparent animationType="fade" onRequestClose={() => setFilterOpen(false)}>
        <TouchableOpacity style={styles.modalBackdrop} activeOpacity={1} onPress={() => setFilterOpen(false)}>
          <View style={styles.sheet}>
            <View style={styles.sheetHandle} />
            <Text style={styles.sheetTitle}>Filter by Category</Text>
            <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 7 }}>
              <TouchableOpacity style={[styles.chip, !category && styles.chipActive]} onPress={() => { setCategory(""); setFilterOpen(false); }}>
                <Text style={[styles.chipText, !category && styles.chipTextActive]}>All</Text>
              </TouchableOpacity>
              {categories.map((c) => (
                <TouchableOpacity key={c} style={[styles.chip, category === c && styles.chipActive]} onPress={() => { setCategory(c); setFilterOpen(false); }}>
                  <Text style={[styles.chipText, category === c && styles.chipTextActive]}>{c}</Text>
                </TouchableOpacity>
              ))}
            </View>
          </View>
        </TouchableOpacity>
      </Modal>

      {/* Viewer sheet */}
      <Modal visible={!!viewer} transparent animationType="fade" onRequestClose={() => setViewer(null)}>
        <TouchableOpacity style={styles.modalBackdropDark} activeOpacity={1} onPress={() => setViewer(null)}>
          <View style={styles.sheet}>
            <View style={styles.sheetHandle} />
            {viewer?.media_kind === "image" && resolveUrl(viewer.file_url ?? viewer.external_url) ? (
              <Image source={{ uri: resolveUrl(viewer.file_url ?? viewer.external_url)! }} style={styles.viewerImage} resizeMode="contain" />
            ) : (
              <Text style={{ fontSize: 34, textAlign: "center", marginBottom: 10 }}>{viewer ? KIND_META[viewer.media_kind].icon : ""}</Text>
            )}
            <Text style={styles.viewerTitle}>{viewer?.title}</Text>
            <Text style={styles.viewerSub}>{viewer ? KIND_META[viewer.media_kind].label : ""} preview</Text>
            {viewer && viewer.media_kind !== "image" && (viewer.file_url || viewer.external_url) ? (
              <TouchableOpacity
                style={styles.openButton}
                onPress={() => {
                  const url = resolveUrl(viewer.file_url ?? viewer.external_url);
                  if (url) Linking.openURL(url).catch(() => {});
                }}
              >
                <Text style={styles.openButtonText}>Open</Text>
              </TouchableOpacity>
            ) : null}
            <TouchableOpacity style={styles.closeButton} onPress={() => setViewer(null)}>
              <Text style={styles.closeButtonText}>Close</Text>
            </TouchableOpacity>
          </View>
        </TouchableOpacity>
      </Modal>
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
    subtitle: { fontSize: 11, color: colors.textMuted },
    filterButton: { width: 34, height: 34, borderRadius: 10, backgroundColor: colors.cardBg, borderWidth: 1, borderColor: colors.border, alignItems: "center", justifyContent: "center" },
    filterDot: { position: "absolute", top: -2, right: -2, width: 8, height: 8, borderRadius: 4, backgroundColor: colors.teal },
    kindRow: { flexGrow: 0, marginTop: 6, marginBottom: 10 },
    chip: { paddingVertical: 7, paddingHorizontal: 13, borderRadius: 99, backgroundColor: colors.screenBg, borderWidth: 1, borderColor: colors.border },
    chipActive: { backgroundColor: colors.teal, borderColor: colors.teal },
    chipText: { fontSize: 11.5, fontWeight: "600", color: colors.textSecondary },
    chipTextActive: { color: "#0b0f1a" },
    error: { color: "#f87185", fontSize: 13, marginHorizontal: 20, marginTop: 12 },
    emptyText: { fontSize: 12.5, color: colors.textMuted, textAlign: "center", marginTop: 40 },
    body: { flex: 1 },
    bodyContent: { paddingHorizontal: 20, paddingBottom: 40 },
    card: { backgroundColor: colors.cardBg, borderWidth: 1, borderColor: colors.border, borderRadius: 14, padding: 14, marginBottom: 10 },
    cardTitle: { fontSize: 13.5, fontWeight: "700", color: colors.textPrimary },
    cardMeta: { fontSize: 11.5, color: colors.textSecondary, marginTop: 2 },
    cardFooter: { flexDirection: "row", marginTop: 10 },
    badge: { paddingVertical: 3, paddingHorizontal: 8, borderRadius: 6 },
    badgeText: { fontSize: 10.5, fontWeight: "700" },
    modalBackdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.55)", justifyContent: "flex-end" },
    modalBackdropDark: { flex: 1, backgroundColor: "rgba(0,0,0,0.7)", justifyContent: "flex-end" },
    sheet: { backgroundColor: colors.cardBg, borderTopLeftRadius: 20, borderTopRightRadius: 20, borderWidth: 1, borderColor: colors.border, padding: 20, paddingBottom: 30 },
    sheetHandle: { width: 36, height: 4, borderRadius: 2, backgroundColor: colors.border, alignSelf: "center", marginBottom: 16 },
    sheetTitle: { fontSize: 15, fontWeight: "700", color: colors.textPrimary, marginBottom: 14 },
    viewerImage: { width: "100%", height: 220, borderRadius: 12, marginBottom: 12, backgroundColor: colors.screenBg },
    viewerTitle: { fontSize: 15, fontWeight: "700", color: colors.textPrimary, textAlign: "center" },
    viewerSub: { fontSize: 12, color: colors.textMuted, textAlign: "center", marginTop: 4, marginBottom: 18 },
    openButton: { height: 44, borderRadius: 10, backgroundColor: colors.teal, alignItems: "center", justifyContent: "center", marginBottom: 10 },
    openButtonText: { fontSize: 13, fontWeight: "700", color: "#0b0f1a" },
    closeButton: { height: 44, borderRadius: 10, backgroundColor: colors.screenBg, borderWidth: 1, borderColor: colors.border, alignItems: "center", justifyContent: "center" },
    closeButtonText: { fontSize: 13, fontWeight: "700", color: colors.textPrimary },
  });
}
