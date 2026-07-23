import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  ActivityIndicator,
  Alert,
  Animated,
  Easing,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { Ionicons } from "@expo/vector-icons";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";

import { ApiError } from "../api/client";
import { login } from "../api/auth";
import { getRememberedLogin, setRememberedLogin } from "../api/rememberedLogin";
import { consumePendingPushTarget, registerForPushNotificationsAsync } from "../notifications/push";
import type { AuthStackParamList } from "../navigation/AuthNavigator";

const NAVY = "#0B1120";
const TEAL = "#2DD4BF";
const INDIGO = "#6657F2";
const SHEET_HEIGHT = 460;
const SPLASH_MS = 1150;

const FORGOT_STEPS = [
  { num: 1, title: "Contact your organization admin", body: "Ask them to go to the Employees page and generate a temporary password for your account." },
  { num: 2, title: "Sign in with the temporary password", body: "Use the temporary password you receive to sign in, then change it from your profile immediately." },
  { num: 3, title: "No admin access? Contact platform support", body: "Reach out to your OmniFlow account manager for help if you are the admin and are locked out." },
];

const CONTACT_STEPS = [
  { num: 1, title: "Organization Admin", body: "Your organization admin can reset passwords, create accounts, and manage roles on the Employees page." },
  { num: 2, title: "Platform Support", body: "If your admin is unavailable or you are the admin and are locked out, contact OmniFlow support directly." },
];

function InfoSheet({
  visible,
  onClose,
  icon,
  title,
  intro,
  steps,
  children,
}: {
  visible: boolean;
  onClose: () => void;
  icon: string;
  title: string;
  intro: string;
  steps: { num: number; title: string; body: string }[];
  children?: ReactNode;
}) {
  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <Pressable style={sheetStyles.backdrop} onPress={onClose} />
      <View style={sheetStyles.sheet}>
        <View style={sheetStyles.handle} />
        <Text style={sheetStyles.title}>{icon} {title}</Text>
        <Text style={sheetStyles.intro}>{intro}</Text>
        {steps.map((s) => (
          <View key={s.num} style={sheetStyles.step}>
            <View style={sheetStyles.stepNum}>
              <Text style={sheetStyles.stepNumText}>{s.num}</Text>
            </View>
            <View style={sheetStyles.stepBody}>
              <Text style={sheetStyles.stepTitle}>{s.title}</Text>
              <Text style={sheetStyles.stepText}>{s.body}</Text>
            </View>
          </View>
        ))}
        {children}
        <TouchableOpacity style={sheetStyles.closeButton} onPress={onClose}>
          <Text style={sheetStyles.closeButtonText}>Close</Text>
        </TouchableOpacity>
      </View>
    </Modal>
  );
}

type Props = NativeStackScreenProps<AuthStackParamList, "Login">;

export default function LoginScreen({ navigation }: Props) {
  const [stage, setStage] = useState<"splash" | "form">("splash");
  const [slug, setSlug] = useState("");
  const [phone, setPhone] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [modal, setModal] = useState<"forgot" | "contact" | null>(null);

  const sheetY = useRef(new Animated.Value(SHEET_HEIGHT)).current;
  const splashOpacity = useRef(new Animated.Value(1)).current;
  const loaderWidth = useRef(new Animated.Value(0)).current;
  const floatY = useRef(new Animated.Value(0)).current;

  const advanceToForm = () => {
    if (stage !== "splash") return;
    setStage("form");
  };

  useEffect(() => {
    getRememberedLogin().then((remembered) => {
      if (remembered) {
        setSlug(remembered.slug);
        setPhone(remembered.phone);
      }
    });
  }, []);

  useEffect(() => {
    const timer = setTimeout(advanceToForm, SPLASH_MS);
    Animated.timing(loaderWidth, {
      toValue: 1,
      duration: SPLASH_MS,
      easing: Easing.linear,
      useNativeDriver: false,
    }).start();
    const floatLoop = Animated.loop(
      Animated.sequence([
        Animated.timing(floatY, { toValue: -7, duration: 2000, easing: Easing.inOut(Easing.sin), useNativeDriver: true }),
        Animated.timing(floatY, { toValue: 0, duration: 2000, easing: Easing.inOut(Easing.sin), useNativeDriver: true }),
      ]),
    );
    floatLoop.start();
    return () => {
      clearTimeout(timer);
      floatLoop.stop();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    Animated.parallel([
      Animated.timing(sheetY, {
        toValue: stage === "form" ? 0 : SHEET_HEIGHT,
        duration: 500,
        easing: Easing.bezier(0.16, 1, 0.3, 1),
        useNativeDriver: true,
      }),
      Animated.timing(splashOpacity, {
        toValue: stage === "form" ? 0 : 1,
        duration: 350,
        useNativeDriver: true,
      }),
    ]).start();
  }, [stage, sheetY, splashOpacity]);

  const onSubmit = async () => {
    setError(null);
    setLoading(true);
    try {
      const trimmedSlug = slug.trim();
      const trimmedPhone = phone.trim();
      const res = await login({ slug: trimmedSlug, phone: trimmedPhone, password });

      const remembered = await getRememberedLogin();
      const alreadyRemembered = remembered?.slug === trimmedSlug && remembered?.phone === trimmedPhone;
      if (!alreadyRemembered) {
        await new Promise<void>((resolve) => {
          Alert.alert(
            "Save your details?",
            "Remember your Factory ID and phone number on this device for next time? Your password itself is never stored by the app — save it in your device's password manager if prompted separately.",
            [
              { text: "Not now", style: "cancel", onPress: () => resolve() },
              {
                text: "Save",
                onPress: async () => {
                  await setRememberedLogin(trimmedSlug, trimmedPhone);
                  resolve();
                },
              },
            ],
          );
        });
      }

      // Fire-and-forget — never let push setup delay or block getting into
      // the app; registerForPushNotificationsAsync is itself best-effort and
      // only ever prompts for OS permission once (see push.ts).
      registerForPushNotificationsAsync();

      // If a push notification tap is what launched the (previously killed)
      // app, jump straight to what it pointed at instead of Home.
      const pending = consumePendingPushTarget();
      if (pending?.link_type === "ticket" && pending.link_id) {
        navigation.replace("Home", { user: res.user, slug: trimmedSlug });
        navigation.navigate("TicketDetail", { user: res.user, ticketId: pending.link_id });
        return;
      }

      navigation.replace("Home", { user: res.user, slug: trimmedSlug });
    } catch (e) {
      if (e instanceof ApiError) {
        if (e.status === 0) {
          setError("Network error — check your connection and try again.");
        } else if (e.detail === "Factory not found") {
          setError("Factory ID not found. Double-check it and try again.");
        } else if (e.detail === "Invalid credentials") {
          setError("Incorrect phone number or password.");
        } else {
          setError(e.detail);
        }
      } else {
        setError("Something went wrong. Please try again.");
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <Pressable style={styles.screen} onPress={advanceToForm} disabled={stage === "form"}>
      {/* Ambient glow blobs — flat translucent circles approximating the
          design's blurred radial-gradient blobs; RN has no CSS blur/radial
          gradient without an extra native lib, so this is a simplification. */}
      <View style={[styles.blob, styles.blobTeal]} />
      <View style={[styles.blob, styles.blobIndigo]} />

      <View style={styles.brandRow}>
        <LinearGradient colors={[TEAL, INDIGO]} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={styles.brandMark} />
        <Text style={styles.brandText}>OmniFlow</Text>
      </View>

      <Animated.View style={[styles.splash, { opacity: splashOpacity }]} pointerEvents={stage === "splash" ? "auto" : "none"}>
        <Animated.View style={[styles.logoMarkWrap, { transform: [{ translateY: floatY }] }]}>
          <LinearGradient colors={[TEAL, INDIGO]} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={styles.logoMark}>
            <Text style={styles.logoMarkText}>OF</Text>
          </LinearGradient>
        </Animated.View>
        <Text style={styles.splashTitle}>OmniFlow</Text>
        <Text style={styles.splashSubtitle}>Workforce & operations, in one app</Text>
        <View style={styles.loaderTrack}>
          <Animated.View
            style={[
              styles.loaderBar,
              { width: loaderWidth.interpolate({ inputRange: [0, 1], outputRange: ["0%", "100%"] }) },
            ]}
          />
        </View>
        <Text style={styles.tapHint}>Tap to continue</Text>
      </Animated.View>

      <KeyboardAvoidingView
        style={styles.sheetLayer}
        behavior={Platform.OS === "ios" ? "padding" : "height"}
        pointerEvents="box-none"
      >
        <Animated.View style={[styles.sheet, { transform: [{ translateY: sheetY }] }]}>
          <View style={styles.sheetHandle} />
          <ScrollView
            style={styles.sheetScroll}
            contentContainerStyle={styles.sheetContent}
            keyboardShouldPersistTaps="handled"
            showsVerticalScrollIndicator={false}
          >
            <Text style={styles.sheetTitle}>Sign in</Text>
            <Text style={styles.sheetSubtitle}>Enter your workspace credentials to continue.</Text>

            <View style={styles.field}>
              <Text style={styles.fieldLabel}>Factory ID</Text>
              <TextInput
                style={styles.input}
                placeholder="e.g. vantage-apparel"
                placeholderTextColor="#64748b"
                autoCapitalize="none"
                autoCorrect={false}
                value={slug}
                onChangeText={setSlug}
                editable={!loading}
              />
            </View>

            <View style={styles.field}>
              <Text style={styles.fieldLabel}>Phone number</Text>
              <TextInput
                style={styles.input}
                placeholder="10-digit phone number"
                placeholderTextColor="#64748b"
                keyboardType="phone-pad"
                autoCapitalize="none"
                autoCorrect={false}
                textContentType="username"
                autoComplete="username"
                value={phone}
                onChangeText={setPhone}
                editable={!loading}
              />
            </View>

            <View style={styles.field}>
              <Text style={styles.fieldLabel}>Password</Text>
              <View style={styles.passwordRow}>
                <TextInput
                  style={[styles.input, styles.passwordInput]}
                  placeholder="••••••••"
                  placeholderTextColor="#64748b"
                  secureTextEntry={!showPassword}
                  textContentType="password"
                  autoComplete="password"
                  value={password}
                  onChangeText={setPassword}
                  editable={!loading}
                />
                <TouchableOpacity
                  style={styles.eyeButton}
                  onPress={() => setShowPassword((v) => !v)}
                  hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
                >
                  <Ionicons name={showPassword ? "eye-off-outline" : "eye-outline"} size={19} color="#94a3b8" />
                </TouchableOpacity>
              </View>
            </View>

            {error ? <Text style={styles.error}>{error}</Text> : null}

            <TouchableOpacity
              style={[styles.signInButton, loading && styles.signInButtonLoading]}
              onPress={onSubmit}
              disabled={loading || !slug || !phone || !password}
            >
              {loading ? <ActivityIndicator color="#fff" /> : <Text style={styles.signInButtonText}>Sign In</Text>}
            </TouchableOpacity>
            <View style={styles.footerLinks}>
              <TouchableOpacity onPress={() => setModal("forgot")}>
                <Text style={styles.footerLinkAccent}>Forgot password?</Text>
              </TouchableOpacity>
              <TouchableOpacity onPress={() => setModal("contact")}>
                <Text style={styles.footerLinkAccent}>Contact your administrator</Text>
              </TouchableOpacity>
              <Text style={styles.footerHint}>
                New here? <Text style={styles.footerLinkAccent} onPress={() => navigation.navigate("Register")}>Create account</Text>
              </Text>
            </View>
          </ScrollView>
        </Animated.View>
      </KeyboardAvoidingView>

      <InfoSheet
        visible={modal === "forgot"}
        onClose={() => setModal(null)}
        icon="🔐"
        title="Reset Your Password"
        intro="Passwords can't be reset by yourself for security reasons. Your organization admin or platform support can generate a temporary password for you."
        steps={FORGOT_STEPS}
      />
      <InfoSheet
        visible={modal === "contact"}
        onClose={() => setModal(null)}
        icon="📞"
        title="Contact Your Administrator"
        intro="Reach out to your organization administrator for account help, access issues, or onboarding support."
        steps={CONTACT_STEPS}
      >
        <View style={sheetStyles.contactButton}>
          <Text style={sheetStyles.contactButtonText}>📞 Call · +91 99714 53045</Text>
        </View>
        <View style={[sheetStyles.contactButton, sheetStyles.contactButtonSecondary]}>
          <Text style={sheetStyles.contactButtonSecondaryText}>✉️ Email · customersupport@omniqconsulting.com</Text>
        </View>
      </InfoSheet>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: NAVY,
    overflow: "hidden",
  },
  blob: {
    position: "absolute",
    borderRadius: 999,
  },
  blobTeal: {
    top: -60,
    right: -70,
    width: 240,
    height: 240,
    backgroundColor: "rgba(45,212,191,0.16)",
  },
  blobIndigo: {
    bottom: 0,
    left: -90,
    width: 260,
    height: 260,
    backgroundColor: "rgba(102,87,242,0.18)",
  },
  brandRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 9,
    paddingTop: 58,
    paddingHorizontal: 24,
  },
  brandMark: {
    width: 28,
    height: 28,
    borderRadius: 9,
  },
  brandText: {
    fontSize: 14,
    fontWeight: "700",
    color: "#f1f5f9",
  },
  splash: {
    ...StyleSheet.absoluteFillObject,
    alignItems: "center",
    justifyContent: "center",
  },
  logoMarkWrap: {
    marginBottom: 18,
  },
  logoMark: {
    width: 64,
    height: 64,
    borderRadius: 19,
    alignItems: "center",
    justifyContent: "center",
  },
  logoMarkText: {
    fontSize: 23,
    fontWeight: "800",
    color: "#fff",
  },
  splashTitle: {
    fontSize: 24,
    fontWeight: "800",
    color: "#f1f5f9",
  },
  splashSubtitle: {
    fontSize: 13,
    color: "#94a3b8",
    marginTop: 6,
    maxWidth: 230,
    textAlign: "center",
  },
  loaderTrack: {
    width: 130,
    height: 3,
    borderRadius: 2,
    backgroundColor: "rgba(255,255,255,0.1)",
    marginTop: 34,
    overflow: "hidden",
  },
  loaderBar: {
    height: "100%",
    backgroundColor: TEAL,
  },
  tapHint: {
    fontSize: 11,
    color: "#475569",
    marginTop: 14,
  },
  sheetLayer: {
    ...StyleSheet.absoluteFillObject,
    justifyContent: "flex-end",
  },
  sheet: {
    maxHeight: "88%",
    backgroundColor: "#111827",
    borderTopLeftRadius: 28,
    borderTopRightRadius: 28,
    borderTopWidth: 1,
    borderColor: "rgba(255,255,255,0.08)",
  },
  sheetHandle: {
    width: 36,
    height: 4,
    borderRadius: 3,
    backgroundColor: "rgba(255,255,255,0.15)",
    alignSelf: "center",
    marginTop: 12,
    marginBottom: 18,
  },
  sheetScroll: {
    flexGrow: 0,
  },
  sheetContent: {
    paddingHorizontal: 26,
    paddingBottom: 28,
  },
  sheetTitle: {
    fontSize: 19,
    fontWeight: "800",
    color: "#f1f5f9",
  },
  sheetSubtitle: {
    fontSize: 12.5,
    color: "#94a3b8",
    marginTop: 3,
    marginBottom: 22,
  },
  field: {
    marginBottom: 14,
  },
  fieldLabel: {
    fontSize: 12,
    fontWeight: "600",
    color: "#94a3b8",
    marginBottom: 6,
  },
  input: {
    height: 48,
    borderRadius: 12,
    backgroundColor: "#0d1424",
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.1)",
    color: "#e2e8f0",
    fontSize: 14.5,
    paddingHorizontal: 14,
  },
  passwordRow: {
    position: "relative",
    justifyContent: "center",
  },
  passwordInput: {
    paddingRight: 44,
  },
  eyeButton: {
    position: "absolute",
    right: 6,
    width: 36,
    height: 36,
    borderRadius: 9,
    alignItems: "center",
    justifyContent: "center",
  },
  error: {
    color: "#f87185",
    fontSize: 13,
    marginBottom: 8,
  },
  signInButton: {
    marginTop: 12,
    height: 50,
    borderRadius: 12,
    backgroundColor: INDIGO,
    alignItems: "center",
    justifyContent: "center",
  },
  signInButtonLoading: {
    opacity: 0.9,
  },
  signInButtonText: {
    color: "#fff",
    fontSize: 15,
    fontWeight: "700",
  },
  footerLinks: {
    alignItems: "center",
    gap: 9,
    marginTop: 16,
  },
  footerLinkAccent: {
    fontSize: 11.5,
    color: TEAL,
    fontWeight: "600",
  },
  footerHint: {
    textAlign: "center",
    fontSize: 11.5,
    color: "#64748b",
    lineHeight: 18,
  },
});

const sheetStyles = StyleSheet.create({
  backdrop: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: "rgba(0,0,0,0.55)",
  },
  sheet: {
    position: "absolute",
    left: 0,
    right: 0,
    bottom: 0,
    maxHeight: "82%",
    backgroundColor: "#111827",
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.08)",
    paddingHorizontal: 20,
    paddingTop: 18,
    paddingBottom: 26,
  },
  handle: {
    width: 36,
    height: 4,
    borderRadius: 2,
    backgroundColor: "rgba(255,255,255,0.15)",
    alignSelf: "center",
    marginBottom: 16,
  },
  title: {
    fontSize: 15,
    fontWeight: "700",
    color: "#f1f5f9",
    marginBottom: 6,
  },
  intro: {
    fontSize: 12.5,
    color: "#94a3b8",
    lineHeight: 19,
    marginBottom: 14,
  },
  step: {
    flexDirection: "row",
    gap: 10,
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: "rgba(255,255,255,0.08)",
  },
  stepNum: {
    width: 22,
    height: 22,
    borderRadius: 99,
    backgroundColor: "rgba(59,130,246,0.16)",
    alignItems: "center",
    justifyContent: "center",
    marginTop: 1,
  },
  stepNumText: {
    fontSize: 11,
    fontWeight: "700",
    color: "#60a5fa",
  },
  stepBody: { flex: 1 },
  stepTitle: {
    fontSize: 12,
    fontWeight: "700",
    color: "#e2e8f0",
    marginBottom: 2,
  },
  stepText: {
    fontSize: 12,
    color: "#94a3b8",
    lineHeight: 17,
  },
  contactButton: {
    alignItems: "center",
    justifyContent: "center",
    borderRadius: 9,
    paddingVertical: 10,
    backgroundColor: "#3b82f6",
    marginTop: 8,
  },
  contactButtonText: {
    fontSize: 12.5,
    fontWeight: "700",
    color: "#fff",
  },
  contactButtonSecondary: {
    backgroundColor: "#1e293b",
  },
  contactButtonSecondaryText: {
    fontSize: 12.5,
    fontWeight: "700",
    color: "#f1f5f9",
  },
  closeButton: {
    marginTop: 16,
    borderRadius: 9,
    paddingVertical: 11,
    backgroundColor: "#1e293b",
    alignItems: "center",
    justifyContent: "center",
  },
  closeButtonText: {
    fontSize: 13,
    fontWeight: "700",
    color: "#f1f5f9",
  },
});
