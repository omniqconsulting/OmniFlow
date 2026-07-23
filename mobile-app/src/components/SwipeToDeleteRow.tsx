import { useRef } from "react";
import { Animated, PanResponder, StyleSheet, Text, View } from "react-native";

const DISMISS_THRESHOLD = -70;
const MAX_SWIPE = -90;

type Props = {
  onDismiss: () => void;
  children: React.ReactNode;
};

// Manual swipe-to-delete (no react-native-gesture-handler dependency in this
// project) — mirrors the design's onPointerDown/Move/Up drag behavior: drag
// left reveals a red "Delete" backdrop, release past threshold dismisses,
// otherwise it springs back.
export default function SwipeToDeleteRow({ onDismiss, children }: Props) {
  const translateX = useRef(new Animated.Value(0)).current;
  const currentX = useRef(0);

  const panResponder = useRef(
    PanResponder.create({
      onMoveShouldSetPanResponder: (_e, gesture) => Math.abs(gesture.dx) > 8 && Math.abs(gesture.dx) > Math.abs(gesture.dy),
      onPanResponderMove: (_e, gesture) => {
        const next = Math.max(MAX_SWIPE, Math.min(0, gesture.dx));
        translateX.setValue(next);
        currentX.current = next;
      },
      onPanResponderRelease: () => {
        if (currentX.current < DISMISS_THRESHOLD) {
          onDismiss();
          return;
        }
        Animated.spring(translateX, { toValue: 0, useNativeDriver: true }).start();
        currentX.current = 0;
      },
      onPanResponderTerminate: () => {
        Animated.spring(translateX, { toValue: 0, useNativeDriver: true }).start();
        currentX.current = 0;
      },
    })
  ).current;

  return (
    <View style={styles.outer}>
      <View style={styles.dismissBg}>
        <Text style={styles.dismissText}>Delete</Text>
      </View>
      <Animated.View style={{ transform: [{ translateX }] }} {...panResponder.panHandlers}>
        {children}
      </Animated.View>
    </View>
  );
}

const styles = StyleSheet.create({
  outer: { position: "relative", borderRadius: 14, overflow: "hidden", marginBottom: 10 },
  dismissBg: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: "#ef4444", flexDirection: "row", alignItems: "center", justifyContent: "flex-end", paddingHorizontal: 20,
  },
  dismissText: { color: "#fff", fontSize: 13, fontWeight: "700" },
});
