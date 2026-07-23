import { useEffect, useRef } from "react";
import { StatusBar } from "expo-status-bar";
import { NavigationContainer, createNavigationContainerRef } from "@react-navigation/native";

import AuthNavigator, { type AuthStackParamList } from "./src/navigation/AuthNavigator";
import { ThemeProvider, useTheme } from "./src/theme/ThemeContext";
import { addNotificationTapListener, checkLaunchedFromNotification, setPendingPushTarget } from "./src/notifications/push";

export const navigationRef = createNavigationContainerRef<AuthStackParamList>();

// Navigates a tapped notification's target to the ticket detail screen when
// we already have a logged-in session in view; otherwise stashes it for
// LoginScreen/HomeScreen to consume once a session exists (see push.ts).
function handlePushTarget(target: { link_type: "ticket" | "none"; link_id: string | null }) {
  if (target.link_type !== "ticket" || !target.link_id) return;
  if (navigationRef.isReady()) {
    const state = navigationRef.getRootState();
    const currentRoute = state.routes[state.routes.length - 1];
    const user = (currentRoute?.params as { user?: AuthStackParamList["Home"]["user"] } | undefined)?.user;
    if (user) {
      navigationRef.navigate("TicketDetail", { user, ticketId: target.link_id });
      return;
    }
  }
  setPendingPushTarget(target);
}

function AppShell() {
  const { colors } = useTheme();
  const startedRef = useRef(false);

  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;

    checkLaunchedFromNotification().then((target) => {
      if (target) handlePushTarget(target);
    });

    const sub = addNotificationTapListener(handlePushTarget);
    return () => sub.remove();
  }, []);

  return (
    <NavigationContainer ref={navigationRef}>
      <AuthNavigator />
      <StatusBar style={colors.mode === "dark" ? "light" : "dark"} />
    </NavigationContainer>
  );
}

export default function App() {
  return (
    <ThemeProvider>
      <AppShell />
    </ThemeProvider>
  );
}
