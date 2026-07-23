import type { ReactNode } from "react";
import { KeyboardAvoidingView, Platform, Pressable, ScrollView, StyleSheet, View } from "react-native";

// Anchors content to the bottom of the screen via absolute positioning
// (mirrors the design's own sheetStyle/detailSheetStyle) instead of RN's
// <Modal>, whose react-native-web implementation centers content and
// ignores our flex-end wrapper — that's what made these sheets "fly to
// the center" when running as a PWA.
type Props = {
  visible: boolean;
  onClose: () => void;
  children: ReactNode;
};

export default function BottomSheet({ visible, onClose, children }: Props) {
  if (!visible) return null;
  return (
    <View style={styles.backdropWrap} pointerEvents="box-none">
      <Pressable style={StyleSheet.absoluteFill} onPress={onClose} />
      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : "height"}
        style={styles.sheet}
      >
        <View style={styles.grabber} />
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          {children}
        </ScrollView>
      </KeyboardAvoidingView>
    </View>
  );
}

const styles = StyleSheet.create({
  backdropWrap: {
    position: "absolute", top: 0, left: 0, right: 0, bottom: 0,
    backgroundColor: "rgba(0,0,0,0.55)", justifyContent: "flex-end", zIndex: 50,
  },
  sheet: {
    maxHeight: "85%", backgroundColor: "#111827",
    borderTopLeftRadius: 24, borderTopRightRadius: 24,
    borderTopWidth: 1, borderColor: "rgba(255,255,255,0.08)",
  },
  grabber: { width: 36, height: 4, borderRadius: 3, backgroundColor: "rgba(255,255,255,0.15)", alignSelf: "center", marginTop: 12, marginBottom: 4 },
  content: { padding: 22, paddingBottom: 34 },
});
