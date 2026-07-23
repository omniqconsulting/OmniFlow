import { clearTokens, getAccessToken, getRefreshToken, storeTokens } from "./tokenStorage";

export const API_BASE_URL = process.env.EXPO_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

// Single in-flight refresh, shared by any request that races into a 401 at
// the same time (avoids burning through refresh-token rotations).
let refreshInFlight: Promise<string | null> | null = null;

async function refreshAccessToken(): Promise<string | null> {
  if (!refreshInFlight) {
    refreshInFlight = (async () => {
      const refreshToken = await getRefreshToken();
      if (!refreshToken) return null;
      try {
        const res = await fetch(`${API_BASE_URL}/api/v1/auth/refresh`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ refresh_token: refreshToken }),
        });
        if (!res.ok) {
          await clearTokens();
          return null;
        }
        const body = await res.json();
        await storeTokens(body.access_token, body.refresh_token);
        return body.access_token as string;
      } catch {
        return null;
      }
    })();
  }
  const token = await refreshInFlight;
  refreshInFlight = null;
  return token;
}

async function withAuthAndRetry(
  doFetch: (accessToken: string | null) => Promise<Response>,
  auth: boolean,
): Promise<Response> {
  let accessToken = auth ? await getAccessToken() : null;
  let res: Response;
  try {
    res = await doFetch(accessToken);
  } catch {
    throw new ApiError(0, "Network error — check your connection and try again.");
  }

  if (res.status === 401 && auth) {
    accessToken = await refreshAccessToken();
    if (accessToken) {
      try {
        res = await doFetch(accessToken);
      } catch {
        throw new ApiError(0, "Network error — check your connection and try again.");
      }
    }
  }
  return res;
}

async function parseOrThrow<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const errBody = await res.json();
      if (typeof errBody?.detail === "string") detail = errBody.detail;
    } catch {
      // non-JSON error body, keep default detail
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

type RequestOptions = {
  method?: string;
  body?: unknown;
  auth?: boolean; // defaults to true
};

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, auth = true } = options;

  const doFetch = async (accessToken: string | null) => {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
    return fetch(`${API_BASE_URL}${path}`, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  };

  const res = await withAuthAndRetry(doFetch, auth);
  return parseOrThrow<T>(res);
}

// Multipart upload — used for punch-in/out (photo) and ticket/checklist
// attachments. Do not set Content-Type manually: fetch computes the
// multipart boundary itself from the FormData body.
export async function apiUpload<T>(path: string, formData: FormData, method = "POST"): Promise<T> {
  const doFetch = async (accessToken: string | null) => {
    const headers: Record<string, string> = {};
    if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
    return fetch(`${API_BASE_URL}${path}`, { method, headers, body: formData });
  };

  const res = await withAuthAndRetry(doFetch, true);
  return parseOrThrow<T>(res);
}
