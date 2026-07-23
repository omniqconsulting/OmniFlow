import { useState } from "react";
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
import { changePassword, updateProfile } from "../api/auth";
import { useTheme, type ThemeColors } from "../theme/ThemeContext";
import type { AuthStackParamList } from "../navigation/AuthNavigator";

type Props = NativeStackScreenProps<AuthStackParamList, "Profile">;

export default function ProfileScreen({ navigation, route }: Props) {
  const { colors } = useTheme();
  const styles = makeStyles(colors);
  const { user, slug } = route.params;

  const [name, setName] = useState(user.name);
  const [phone, setPhone] = useState(user.phone);
  const [savingProfile, setSavingProfile] = useState(false);
  const [profileMsg, setProfileMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  const [currentPw, setCurrentPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [confirmPw, setConfirmPw] = useState("");
  const [savingPw, setSavingPw] = useState(false);
  const [pwMsg, setPwMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  const onSaveProfile = async () => {
    setProfileMsg(null);
    setSavingProfile(true);
    try {
      await updateProfile({ name: name.trim(), phone: phone.trim() });
      setProfileMsg({ kind: "ok", text: "Profile updated successfully" });
    } catch (e) {
      setProfileMsg({ kind: "error", text: e instanceof ApiError ? e.detail : "Something went wrong." });
    } finally {
      setSavingProfile(false);
    }
  };

  const onChangePassword = async () => {
    setPwMsg(null);
    if (newPw !== confirmPw) {
      setPwMsg({ kind: "error", text: "New passwords do not match" });
      return;
    }
    setSavingPw(true);
    try {
      await changePassword({ current_password: currentPw, new_password: newPw, confirm_password: confirmPw });
      setPwMsg({ kind: "ok", text: "Password changed successfully" });
      setCurrentPw("");
      setNewPw("");
      setConfirmPw("");
    } catch (e) {
      setPwMsg({ kind: "error", text: e instanceof ApiError ? e.detail : "Something went wrong." });
    } finally {
      setSavingPw(false);
    }
  };

  const pwMatchVisible = !!confirmPw;
  const pwMatch = newPw === confirmPw;

  return (
    <View style={styles.screen}>
      <View style={styles.header}>
        <TouchableOpacity style={styles.backButton} onPress={() => navigation.goBack()} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
          <Ionicons name="chevron-back" size={20} color={colors.textSecondary} />
        </TouchableOpacity>
        <View>
          <Text style={styles.headerTitle}>My Profile</Text>
          <Text style={styles.headerSub}>{user.name}</Text>
        </View>
      </View>

      <KeyboardAvoidingView style={styles.flex} behavior={Platform.OS === "ios" ? "padding" : "height"}>
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
          <View style={styles.card}>
            <Text style={styles.sectionLabel}>Profile Details</Text>

            <Text style={styles.fieldLabel}>Full Name</Text>
            <TextInput style={styles.input} value={name} onChangeText={setName} editable={!savingProfile} />

            <Text style={styles.fieldLabel}>Phone Number</Text>
            <View style={styles.phoneRow}>
              <View style={styles.phonePrefix}>
                <Text style={styles.phonePrefixText}>🇮🇳 +91</Text>
              </View>
              <TextInput
                style={[styles.input, styles.phoneInput]}
                placeholder="XXXXXXXXXX"
                placeholderTextColor={colors.textMuted}
                keyboardType="phone-pad"
                value={phone}
                onChangeText={setPhone}
                editable={!savingProfile}
              />
            </View>

            <Text style={styles.fieldLabel}>Role</Text>
            <View style={styles.roleChip}>
              <Text style={styles.roleChipText}>{user.role}</Text>
            </View>

            {profileMsg ? (
              <Text style={[styles.msg, profileMsg.kind === "error" ? styles.msgError : styles.msgOk]}>{profileMsg.text}</Text>
            ) : null}

            <TouchableOpacity style={styles.saveButton} onPress={onSaveProfile} disabled={savingProfile}>
              {savingProfile ? <ActivityIndicator color="#fff" /> : <Text style={styles.saveButtonText}>Save Changes</Text>}
            </TouchableOpacity>
          </View>

          <View style={styles.card}>
            <Text style={styles.sectionLabel}>Change Password</Text>

            <Text style={styles.fieldLabel}>Current Password</Text>
            <TextInput style={styles.input} secureTextEntry placeholder="••••••••" placeholderTextColor={colors.textMuted} value={currentPw} onChangeText={setCurrentPw} editable={!savingPw} />

            <Text style={styles.fieldLabel}>New Password</Text>
            <TextInput style={styles.input} secureTextEntry placeholder="Min. 6 characters" placeholderTextColor={colors.textMuted} value={newPw} onChangeText={setNewPw} editable={!savingPw} />

            <Text style={styles.fieldLabel}>Confirm New Password</Text>
            <TextInput style={styles.input} secureTextEntry placeholder="Repeat new password" placeholderTextColor={colors.textMuted} value={confirmPw} onChangeText={setConfirmPw} editable={!savingPw} />
            {pwMatchVisible ? (
              <Text style={[styles.pwMatch, { color: pwMatch ? "#10b981" : "#f87171" }]}>
                {pwMatch ? "✓ Passwords match" : "✗ Passwords do not match"}
              </Text>
            ) : null}

            {pwMsg ? <Text style={[styles.msg, pwMsg.kind === "error" ? styles.msgError : styles.msgOk]}>{pwMsg.text}</Text> : null}

            <TouchableOpacity
              style={[styles.saveButton, styles.pwButton]}
              onPress={onChangePassword}
              disabled={savingPw || !currentPw || !newPw || !confirmPw}
            >
              {savingPw ? <ActivityIndicator color="#fff" /> : <Text style={styles.saveButtonText}>Change Password</Text>}
            </TouchableOpacity>
          </View>

          <View style={styles.metaRow}>
            <Text style={styles.metaLabel}>Organization ID</Text>
            <Text style={styles.metaValue}>{slug}</Text>
          </View>
          <View style={styles.metaRow}>
            <Text style={styles.metaLabel}>User ID</Text>
            <Text style={styles.metaValue}>{user.id.slice(0, 12)}…</Text>
          </View>
        </ScrollView>
      </KeyboardAvoidingView>
    </View>
  );
}

function makeStyles(colors: ThemeColors) {
  return StyleSheet.create({
    screen: { flex: 1, backgroundColor: colors.screenBg },
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
      backgroundColor: colors.iconButtonBg,
      borderWidth: 1,
      borderColor: colors.border,
      alignItems: "center",
      justifyContent: "center",
    },
    headerTitle: { fontSize: 17.5, fontWeight: "800", color: colors.textPrimary },
    headerSub: { fontSize: 11, color: colors.textMuted, marginTop: 1 },
    content: { paddingHorizontal: 20, paddingBottom: 40 },
    card: {
      backgroundColor: colors.cardBg,
      borderWidth: 1,
      borderColor: colors.border,
      borderRadius: 14,
      padding: 16,
      marginBottom: 14,
    },
    sectionLabel: {
      fontSize: 11,
      fontWeight: "700",
      textTransform: "uppercase",
      letterSpacing: 0.5,
      color: colors.textMuted,
      marginBottom: 12,
    },
    fieldLabel: { fontSize: 11, color: colors.textMuted, marginBottom: 5, marginTop: 2 },
    input: {
      height: 46,
      borderRadius: 10,
      backgroundColor: colors.screenBg,
      borderWidth: 1,
      borderColor: colors.border,
      color: colors.textPrimary,
      fontSize: 13.5,
      paddingHorizontal: 13,
      marginBottom: 14,
    },
    phoneRow: { flexDirection: "row", gap: 8, marginBottom: 14 },
    phonePrefix: {
      width: 64,
      flexShrink: 0,
      backgroundColor: colors.screenBg,
      borderWidth: 1,
      borderColor: colors.border,
      borderRadius: 10,
      alignItems: "center",
      justifyContent: "center",
    },
    phonePrefixText: { fontSize: 13, color: colors.textPrimary },
    phoneInput: { flex: 1, marginBottom: 0 },
    roleChip: {
      backgroundColor: colors.screenBg,
      opacity: 0.75,
      borderWidth: 1,
      borderColor: colors.border,
      borderRadius: 10,
      paddingVertical: 11,
      paddingHorizontal: 13,
      marginBottom: 14,
    },
    roleChipText: { fontSize: 13.5, color: colors.textMuted },
    saveButton: {
      height: 46,
      borderRadius: 10,
      backgroundColor: colors.indigo,
      alignItems: "center",
      justifyContent: "center",
    },
    pwButton: { backgroundColor: "#22c55e" },
    saveButtonText: { fontSize: 13.5, fontWeight: "700", color: "#fff" },
    pwMatch: { fontSize: 11, fontWeight: "600", marginTop: -8, marginBottom: 12 },
    msg: { fontSize: 12, marginBottom: 10 },
    msgOk: { color: "#10b981" },
    msgError: { color: "#f87185" },
    metaRow: {
      flexDirection: "row",
      justifyContent: "space-between",
      paddingHorizontal: 4,
      paddingVertical: 4,
    },
    metaLabel: { fontSize: 11.5, color: colors.textMuted },
    metaValue: { fontSize: 11.5, color: colors.textMuted, fontFamily: Platform.select({ ios: "Menlo", android: "monospace" }) },
  });
}
