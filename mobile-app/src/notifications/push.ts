import { Linking, Platform } from "react-native";
import * as Notifications from "expo-notifications";
import * as Device from "expo-device";
import Constants from "expo-constants";
import * as SecureStore from "expo-secure-store";
import * as Crypto from "expo-crypto";

import { registerDevice, unregisterDevice } from "../api/devices";
import { ApiError } from "../api/client";

const DEVICE_ID_KEY = "omniflow_device_id";
// Set once we've ever shown the OS permission prompt — checked before
// calling requestPermissionsAsync again so a user who dismisses/denies it
// isn't re-prompted on every login. (iOS only shows its native prompt once
// per install anyway; this makes Android behave the same way, and keeps us
// from re-running the whole setup dance needlessly on every app open.)
const PERMISSION_ASKED_KEY = "omniflow_push_permission_asked";

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowBanner: true,
    shouldShowList: true,
    shouldPlaySound: true,
    shouldSetBadge: false,
  }),
});

async function getOrCreateDeviceId(): Promise<string> {
  const existing = await SecureStore.getItemAsync(DEVICE_ID_KEY);
  if (existing) return existing;
  const id = Crypto.randomUUID();
  await SecureStore.setItemAsync(DEVICE_ID_KEY, id);
  return id;
}

async function ensureAndroidChannel() {
  if (Platform.OS !== "android") return;
  await Notifications.setNotificationChannelAsync("default", {
    name: "OmniFlow",
    importance: Notifications.AndroidImportance.HIGH,
    lightColor: "#2DD4BF",
  });
}

/**
 * Registers this device for push once permission is settled — never prompts
 * more than once per install (see PERMISSION_ASKED_KEY). Safe to call on
 * every login: if permission was already asked/granted it just re-syncs the
 * token with the backend (cheap upsert), it never re-shows the OS dialog.
 * Best-effort throughout — a push setup failure must never block login.
 */
export async function registerForPushNotificationsAsync(): Promise<void> {
  try {
    if (!Device.isDevice) {
      // Simulators/emulators can't receive push and getExpoPushTokenAsync
      // throws on them — skip quietly rather than logging noise every run.
      return;
    }

    await ensureAndroidChannel();

    const alreadyAsked = (await SecureStore.getItemAsync(PERMISSION_ASKED_KEY)) === "true";
    let { status } = await Notifications.getPermissionsAsync();

    if (status !== "granted" && !alreadyAsked) {
      const req = await Notifications.requestPermissionsAsync();
      status = req.status;
      await SecureStore.setItemAsync(PERMISSION_ASKED_KEY, "true");
    }

    if (status !== "granted") {
      // Either denied outright, or denied on a prior run — don't nag.
      return;
    }

    const projectId =
      Constants.expoConfig?.extra?.eas?.projectId ?? Constants.easConfig?.projectId;
    if (!projectId || projectId === "REPLACE_WITH_EAS_PROJECT_ID") {
      console.warn(
        "[push] No EAS projectId configured (app.json extra.eas.projectId) — " +
          "run `eas init` and set it, otherwise push tokens can't be generated."
      );
      return;
    }

    const tokenResponse = await Notifications.getExpoPushTokenAsync({ projectId });
    const deviceId = await getOrCreateDeviceId();
    await registerDevice(deviceId, tokenResponse.data, Platform.OS as "ios" | "android");
  } catch (e) {
    // Never let push setup break login/app startup.
    console.warn("[push] registration skipped:", e instanceof ApiError ? e.detail : e);
  }
}

export type PushPermissionStatus = "granted" | "denied" | "undetermined" | "unsupported";

/** Current OS permission state — "unsupported" on simulators/emulators,
 * where there's nothing meaningful to report or act on. Used to drive an
 * "Enable notifications" affordance in the UI (see NotificationsScreen). */
export async function getPushPermissionStatus(): Promise<PushPermissionStatus> {
  if (!Device.isDevice) return "unsupported";
  const { status } = await Notifications.getPermissionsAsync();
  return status;
}

/**
 * User-initiated re-enable, called from a visible "Enable notifications"
 * button — distinct from the silent, once-only auto-prompt on login.
 * - undetermined (never asked, or Android allows asking again): shows the
 *   native OS prompt, same as first login.
 * - denied: iOS/Android will NOT show their prompt again once dismissed —
 *   the only way back in is the device's own Settings app, so this opens it.
 * - granted: just re-syncs the token (e.g. permission was toggled on
 *   outside the app, or the token needs a refresh).
 * Returns the resulting status so the caller can update its UI.
 */
export async function enablePushNotifications(): Promise<PushPermissionStatus> {
  const current = await getPushPermissionStatus();
  if (current === "unsupported") return current;

  if (current === "denied") {
    await Linking.openSettings();
    // We can't know the outcome until the user comes back — caller should
    // re-check getPushPermissionStatus() on next foreground (see
    // NotificationsScreen's AppState listener).
    return current;
  }

  if (current === "undetermined") {
    const req = await Notifications.requestPermissionsAsync();
    await SecureStore.setItemAsync(PERMISSION_ASKED_KEY, "true");
    if (req.status === "granted") await registerForPushNotificationsAsync();
    return req.status;
  }

  // Already granted — just make sure the backend has a current token.
  await registerForPushNotificationsAsync();
  return current;
}

/** Called on logout so a signed-out device stops receiving this user's pushes. */
export async function unregisterCurrentDevice(): Promise<void> {
  try {
    const deviceId = await SecureStore.getItemAsync(DEVICE_ID_KEY);
    if (deviceId) await unregisterDevice(deviceId);
  } catch {
    // Best-effort — logout must proceed either way.
  }
}

export type PushNavigationTarget = { link_type: "ticket" | "none"; link_id: string | null };

// Holds a tap target that arrived before we had anywhere to navigate to yet
// — e.g. the notification tap is what launched the (previously killed) app,
// so there's no logged-in `user` in navigation params for TicketDetail until
// after LoginScreen finishes. LoginScreen checks this once login succeeds.
let pendingTarget: PushNavigationTarget | null = null;

export function setPendingPushTarget(target: PushNavigationTarget) {
  pendingTarget = target;
}

export function consumePendingPushTarget(): PushNavigationTarget | null {
  const t = pendingTarget;
  pendingTarget = null;
  return t;
}

/** Cold-start case: the tap that launched the app doesn't fire the regular
 * response listener — it has to be read explicitly once on startup. */
export async function checkLaunchedFromNotification(): Promise<PushNavigationTarget | null> {
  const response = await Notifications.getLastNotificationResponseAsync();
  const data = response?.notification.request.content.data as Partial<PushNavigationTarget> | undefined;
  if (data?.link_type) {
    return { link_type: data.link_type, link_id: data.link_id ?? null };
  }
  return null;
}

/**
 * Fires when the user taps a push notification (foreground, background, or
 * from a cold start via getLastNotificationResponseAsync — see App.tsx).
 * The `data` payload mirrors the in-app list's link_type/link_id resolution
 * (see app/notifications.py resolve_notification_link) so a tapped push
 * opens the same screen tapping the in-app row would.
 */
export function addNotificationTapListener(onTap: (target: PushNavigationTarget) => void) {
  return Notifications.addNotificationResponseReceivedListener((response) => {
    const data = response.notification.request.content.data as Partial<PushNavigationTarget> | undefined;
    if (data?.link_type) {
      onTap({ link_type: data.link_type, link_id: data.link_id ?? null });
    }
  });
}
