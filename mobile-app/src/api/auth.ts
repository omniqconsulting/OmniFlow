import { apiRequest } from "./client";
import { storeTokens } from "./tokenStorage";

export type LoginPayload = {
  slug: string;
  phone: string;
  password: string;
};

export type SessionUser = { id: string; name: string; phone: string; role: string; tenant_id: string };

export type TokenResponse = {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  user: SessionUser;
};

export async function login(payload: LoginPayload): Promise<TokenResponse> {
  const res = await apiRequest<TokenResponse>("/api/v1/auth/login", {
    method: "POST",
    body: { ...payload, device_label: "expo-app" },
    auth: false,
  });
  await storeTokens(res.access_token, res.refresh_token);
  return res;
}

export type RegisterPayload = {
  factory_name: string;
  slug: string;
  name: string;
  phone: string;
  password: string;
  contact_email?: string;
};

export type RegisterResponse = { tenant_id: string; slug: string; status: string };

export async function register(payload: RegisterPayload): Promise<RegisterResponse> {
  return apiRequest<RegisterResponse>("/api/v1/auth/register", {
    method: "POST",
    body: payload,
    auth: false,
  });
}

export async function checkSlugAvailable(slug: string): Promise<boolean> {
  const res = await apiRequest<{ available: boolean }>(
    `/api/v1/auth/check-slug?slug=${encodeURIComponent(slug)}`,
    { auth: false },
  );
  return res.available;
}

export async function updateProfile(payload: { name: string; phone: string }): Promise<SessionUser> {
  return apiRequest<SessionUser>("/api/v1/auth/profile", {
    method: "PATCH",
    body: payload,
  });
}

export async function changePassword(payload: {
  current_password: string;
  new_password: string;
  confirm_password: string;
}): Promise<void> {
  await apiRequest<{ ok: boolean }>("/api/v1/auth/change-password", {
    method: "POST",
    body: payload,
  });
}
