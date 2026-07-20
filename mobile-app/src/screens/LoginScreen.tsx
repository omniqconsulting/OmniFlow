import { useState } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";

import { ApiError } from "../api/client";
import { login } from "../api/auth";
import type { AuthStackParamList } from "../navigation/AuthNavigator";

const NAVY = "#0B1120";
const TEAL = "#2DD4BF";

type Props = NativeStackScreenProps<AuthStackParamList, "Login">;

export default function LoginScreen({ navigation }: Props) {
  const [slug, setSlug] = useState("");
  const [phone, setPhone] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onSubmit = async () => {
    setError(null);
    setLoading(true);
    try {
      await login({ slug: slug.trim(), phone: phone.trim(), password });
      navigation.replace("LoggedInPlaceholder");
    } catch (e) {
      if (e instanceof ApiError) {
        if (e.status === 0) {
          setError("Network error — check your connection and try again.");
        } else if (e.detail === "Factory not found") {
          setError("Organisation ID not found. Double-check it and try again.");
        } else if (e.detail === "Invalid credentials") {
          setError("Incorrect phone number or password.");
        } else if (e.status === 403) {
          setError(e.detail);
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
    <KeyboardAvoidingView
      style={styles.screen}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
    >
      <View style={styles.card}>
        <Text style={styles.brand}>OmniFlow</Text>
        <Text style={styles.subtitle}>Sign in to continue</Text>

        <TextInput
          style={styles.input}
          placeholder="Organisation ID"
          placeholderTextColor="#8892a6"
          autoCapitalize="none"
          autoCorrect={false}
          value={slug}
          onChangeText={setSlug}
          editable={!loading}
        />
        <TextInput
          style={styles.input}
          placeholder="Phone number"
          placeholderTextColor="#8892a6"
          keyboardType="phone-pad"
          autoCapitalize="none"
          autoCorrect={false}
          value={phone}
          onChangeText={setPhone}
          editable={!loading}
        />
        <TextInput
          style={styles.input}
          placeholder="Password"
          placeholderTextColor="#8892a6"
          secureTextEntry
          value={password}
          onChangeText={setPassword}
          editable={!loading}
        />

        {error ? <Text style={styles.error}>{error}</Text> : null}

        <TouchableOpacity
          style={[styles.button, loading && styles.buttonDisabled]}
          onPress={onSubmit}
          disabled={loading || !slug || !phone || !password}
        >
          {loading ? (
            <ActivityIndicator color={NAVY} />
          ) : (
            <Text style={styles.buttonText}>Log in</Text>
          )}
        </TouchableOpacity>
      </View>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: NAVY,
    justifyContent: "center",
    padding: 24,
  },
  card: {
    borderRadius: 16,
    padding: 24,
    backgroundColor: "#151f38",
  },
  brand: {
    fontSize: 28,
    fontWeight: "700",
    color: TEAL,
    marginBottom: 4,
  },
  subtitle: {
    fontSize: 14,
    color: "#c3c9da",
    marginBottom: 24,
  },
  input: {
    borderWidth: 1,
    borderColor: "#2a3556",
    borderRadius: 10,
    paddingHorizontal: 14,
    paddingVertical: 12,
    color: "#fff",
    marginBottom: 12,
    backgroundColor: "#0f1730",
  },
  error: {
    color: "#f87171",
    marginBottom: 12,
    fontSize: 13,
  },
  button: {
    backgroundColor: TEAL,
    borderRadius: 10,
    paddingVertical: 14,
    alignItems: "center",
    marginTop: 8,
  },
  buttonDisabled: {
    opacity: 0.6,
  },
  buttonText: {
    color: NAVY,
    fontWeight: "700",
    fontSize: 16,
  },
});
