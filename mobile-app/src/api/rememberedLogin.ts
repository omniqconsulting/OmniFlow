import * as SecureStore from "expo-secure-store";

const SLUG_KEY = "omniflow_remembered_slug";
const PHONE_KEY = "omniflow_remembered_phone";

// Deliberately does NOT store the password — only the Factory ID and phone,
// so returning to the login screen can pre-fill those two fields. The
// password itself is left to the OS's own secure credential manager
// (iOS Keychain / Android Autofill), triggered via textContentType/
// autoComplete on the password TextInput — that's the professional,
// secure mechanism for "remember my password", not something an app
// should replicate itself by storing raw passwords in its own storage.
export async function getRememberedLogin(): Promise<{ slug: string; phone: string } | null> {
  const [slug, phone] = await Promise.all([
    SecureStore.getItemAsync(SLUG_KEY),
    SecureStore.getItemAsync(PHONE_KEY),
  ]);
  if (!slug || !phone) return null;
  return { slug, phone };
}

export async function setRememberedLogin(slug: string, phone: string): Promise<void> {
  await Promise.all([
    SecureStore.setItemAsync(SLUG_KEY, slug),
    SecureStore.setItemAsync(PHONE_KEY, phone),
  ]);
}

export async function clearRememberedLogin(): Promise<void> {
  await Promise.all([
    SecureStore.deleteItemAsync(SLUG_KEY),
    SecureStore.deleteItemAsync(PHONE_KEY),
  ]);
}
