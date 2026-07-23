import { useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";

import { ApiError } from "../api/client";
import { checkSlugAvailable, register } from "../api/auth";
import type { AuthStackParamList } from "../navigation/AuthNavigator";

const NAVY = "#0B1120";
const TEAL = "#2DD4BF";
const INDIGO = "#6657F2";

type Props = NativeStackScreenProps<AuthStackParamList, "Register">;

type SlugStatus = "idle" | "checking" | "available" | "taken";

export default function RegisterScreen({ navigation }: Props) {
  const [factoryName, setFactoryName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugStatus, setSlugStatus] = useState<SlugStatus>("idle");
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const slugCheckTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (slugCheckTimer.current) clearTimeout(slugCheckTimer.current);
    const trimmed = slug.trim().toLowerCase();
    if (!trimmed) {
      setSlugStatus("idle");
      return;
    }
    setSlugStatus("checking");
    slugCheckTimer.current = setTimeout(async () => {
      try {
        const available = await checkSlugAvailable(trimmed);
        setSlugStatus(available ? "available" : "taken");
      } catch {
        setSlugStatus("idle");
      }
    }, 450);
    return () => {
      if (slugCheckTimer.current) clearTimeout(slugCheckTimer.current);
    };
  }, [slug]);

  const onSubmit = async () => {
    setError(null);
    setLoading(true);
    try {
      const trimmedSlug = slug.trim().toLowerCase();
      const res = await register({
        factory_name: factoryName.trim(),
        slug: trimmedSlug,
        name: name.trim(),
        phone: phone.trim(),
        password,
        contact_email: email.trim() || undefined,
      });
      if (res.status === "pending_approval") setPending(true);
    } catch (e) {
      if (e instanceof ApiError) {
        if (e.status === 0) {
          setError("Network error — check your connection and try again.");
        } else if (e.status === 409) {
          setError("Factory ID already taken. Try a different Organization ID.");
          setSlugStatus("taken");
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

  const canSubmit = !!(factoryName && slug && slugStatus !== "taken" && name && phone && password.length >= 6);

  if (pending) {
    return (
      <View style={styles.screen}>
        <View style={styles.pendingWrap}>
          <View style={styles.pendingIcon}>
            <Text style={{ fontSize: 28 }}>✓</Text>
          </View>
          <Text style={styles.pendingTitle}>Account submitted</Text>
          <Text style={styles.pendingBody}>
            Your account will be reviewed and activated within a few hours. We'll notify you as
            soon as {factoryName || "your organization"} is approved.
          </Text>
          <TouchableOpacity style={styles.signInButton} onPress={() => navigation.replace("Login")}>
            <Text style={styles.signInButtonText}>Back to Sign In</Text>
          </TouchableOpacity>
        </View>
      </View>
    );
  }

  return (
    <View style={styles.screen}>
      <KeyboardAvoidingView style={styles.flex} behavior={Platform.OS === "ios" ? "padding" : "height"}>
        <View style={styles.header}>
          <TouchableOpacity style={styles.backButton} onPress={() => navigation.goBack()} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
            <Ionicons name="chevron-back" size={20} color="#94a3b8" />
          </TouchableOpacity>
          <View>
            <Text style={styles.headerTitle}>Create Organization</Text>
            <Text style={styles.headerSub}>Admin account setup</Text>
          </View>
        </View>

        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
          <Text style={styles.cardTitle}>Create organization account</Text>
          <Text style={styles.cardSubtitle}>You'll be the Admin — employees can be added after setup</Text>

          <Text style={styles.sectionLabel}>Organization Details</Text>

          <View style={styles.field}>
            <Text style={styles.fieldLabel}>Organization Name</Text>
            <TextInput
              style={styles.input}
              placeholder="Acme Industries Pvt Ltd"
              placeholderTextColor="#64748b"
              value={factoryName}
              onChangeText={setFactoryName}
              editable={!loading}
            />
          </View>

          <View style={styles.field}>
            <Text style={styles.fieldLabel}>Organization ID</Text>
            <TextInput
              style={styles.input}
              placeholder="acme-industries"
              placeholderTextColor="#64748b"
              autoCapitalize="none"
              autoCorrect={false}
              value={slug}
              onChangeText={setSlug}
              editable={!loading}
            />
            <Text style={styles.hint}>Lowercase letters, numbers and hyphens only · Used to login</Text>
            {slugStatus === "checking" ? <Text style={styles.slugChecking}>Checking availability…</Text> : null}
            {slugStatus === "available" ? <Text style={styles.slugAvailable}>✓ Available</Text> : null}
            {slugStatus === "taken" ? <Text style={styles.slugTaken}>✗ Already taken</Text> : null}
          </View>

          <Text style={styles.sectionLabel}>Your Details (Admin Account)</Text>

          <View style={styles.field}>
            <Text style={styles.fieldLabel}>Your Name</Text>
            <TextInput style={styles.input} value={name} onChangeText={setName} editable={!loading} placeholderTextColor="#64748b" />
          </View>

          <View style={styles.field}>
            <Text style={styles.fieldLabel}>Phone Number</Text>
            <View style={styles.phoneRow}>
              <View style={styles.phonePrefix}>
                <Text style={styles.phonePrefixText}>🇮🇳 +91</Text>
              </View>
              <TextInput
                style={[styles.input, styles.phoneInput]}
                placeholder="XXXXXXXXXX"
                placeholderTextColor="#64748b"
                keyboardType="phone-pad"
                value={phone}
                onChangeText={setPhone}
                editable={!loading}
              />
            </View>
          </View>

          <View style={styles.field}>
            <Text style={styles.fieldLabel}>Contact Email (optional)</Text>
            <TextInput
              style={styles.input}
              placeholder="you@company.com"
              placeholderTextColor="#64748b"
              autoCapitalize="none"
              keyboardType="email-address"
              value={email}
              onChangeText={setEmail}
              editable={!loading}
            />
          </View>

          <View style={styles.field}>
            <Text style={styles.fieldLabel}>Password</Text>
            <View style={styles.passwordRow}>
              <TextInput
                style={[styles.input, styles.passwordInput]}
                placeholder="Min. 6 characters"
                placeholderTextColor="#64748b"
                secureTextEntry={!showPassword}
                value={password}
                onChangeText={setPassword}
                editable={!loading}
              />
              <TouchableOpacity style={styles.eyeButton} onPress={() => setShowPassword((v) => !v)} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
                <Ionicons name={showPassword ? "eye-off-outline" : "eye-outline"} size={19} color="#94a3b8" />
              </TouchableOpacity>
            </View>
          </View>

          {error ? <Text style={styles.error}>{error}</Text> : null}

          <TouchableOpacity
            style={[styles.signInButton, (loading || !canSubmit) && styles.signInButtonDisabled]}
            onPress={onSubmit}
            disabled={loading || !canSubmit}
          >
            {loading ? <ActivityIndicator color="#fff" /> : <Text style={styles.signInButtonText}>Create Organization Account →</Text>}
          </TouchableOpacity>
          <Text style={styles.footerHint}>Your account will be reviewed and activated within a few hours</Text>

          <View style={styles.divider} />
          <TouchableOpacity onPress={() => navigation.replace("Login")}>
            <Text style={styles.footerLink}>Already registered? <Text style={styles.footerLinkAccent}>Sign in</Text></Text>
          </TouchableOpacity>
        </ScrollView>
      </KeyboardAvoidingView>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: NAVY },
  flex: { flex: 1 },
  header: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    paddingTop: 58,
    paddingHorizontal: 20,
    paddingBottom: 14,
  },
  backButton: {
    width: 34,
    height: 34,
    borderRadius: 10,
    backgroundColor: "#111827",
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.08)",
    alignItems: "center",
    justifyContent: "center",
  },
  headerTitle: { fontSize: 17.5, fontWeight: "800", color: "#f1f5f9" },
  headerSub: { fontSize: 11, color: "#64748b", marginTop: 1 },
  content: { paddingHorizontal: 20, paddingBottom: 40 },
  cardTitle: { fontSize: 14.5, fontWeight: "700", color: "#f1f5f9" },
  cardSubtitle: { fontSize: 11, color: "#64748b", marginTop: 3, marginBottom: 16 },
  sectionLabel: {
    fontSize: 10,
    fontWeight: "700",
    textTransform: "uppercase",
    letterSpacing: 0.6,
    color: "#64748b",
    marginTop: 6,
    marginBottom: 10,
    paddingBottom: 8,
    borderBottomWidth: 1,
    borderBottomColor: "rgba(255,255,255,0.08)",
  },
  field: { marginBottom: 14 },
  fieldLabel: { fontSize: 12, fontWeight: "600", color: "#94a3b8", marginBottom: 6 },
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
  hint: { fontSize: 10.5, color: "#64748b", marginTop: 4 },
  slugChecking: { fontSize: 11, color: "#64748b", marginTop: 4 },
  slugAvailable: { fontSize: 11, fontWeight: "600", color: "#10b981", marginTop: 4 },
  slugTaken: { fontSize: 11, fontWeight: "600", color: "#f87171", marginTop: 4 },
  phoneRow: { flexDirection: "row", gap: 8 },
  phonePrefix: {
    width: 64,
    height: 48,
    flexShrink: 0,
    backgroundColor: "#0d1424",
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.1)",
    borderRadius: 12,
    alignItems: "center",
    justifyContent: "center",
  },
  phonePrefixText: { fontSize: 12.5, color: "#e2e8f0" },
  phoneInput: { flex: 1 },
  passwordRow: { position: "relative", justifyContent: "center" },
  passwordInput: { paddingRight: 44 },
  eyeButton: {
    position: "absolute",
    right: 6,
    width: 36,
    height: 36,
    borderRadius: 9,
    alignItems: "center",
    justifyContent: "center",
  },
  error: { color: "#f87185", fontSize: 13, marginBottom: 8 },
  signInButton: {
    marginTop: 12,
    height: 50,
    borderRadius: 12,
    backgroundColor: INDIGO,
    alignItems: "center",
    justifyContent: "center",
  },
  signInButtonDisabled: { opacity: 0.5 },
  signInButtonText: { color: "#fff", fontSize: 14.5, fontWeight: "700" },
  footerHint: { textAlign: "center", fontSize: 10.5, color: "#64748b", marginTop: 10 },
  divider: { height: 1, backgroundColor: "rgba(255,255,255,0.08)", marginVertical: 18 },
  footerLink: { textAlign: "center", fontSize: 11.5, color: "#64748b" },
  footerLinkAccent: { color: TEAL, fontWeight: "600" },
  pendingWrap: { flex: 1, alignItems: "center", justifyContent: "center", paddingHorizontal: 32 },
  pendingIcon: {
    width: 64,
    height: 64,
    borderRadius: 32,
    backgroundColor: "rgba(16,185,129,0.16)",
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 18,
  },
  pendingTitle: { fontSize: 19, fontWeight: "800", color: "#f1f5f9", marginBottom: 8 },
  pendingBody: { fontSize: 13, color: "#94a3b8", textAlign: "center", lineHeight: 20, marginBottom: 28 },
});
