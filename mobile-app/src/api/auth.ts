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
