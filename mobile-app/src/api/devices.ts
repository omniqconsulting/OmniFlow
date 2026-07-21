import { apiRequest } from "./client";

export type DeviceRegisterResult = { device_id: string; platform: string; last_seen_at: string };

export function registerDevice(deviceId: string, expoPushToken: string, platform: "ios" | "android"): Promise<DeviceRegisterResult> {
  return apiRequest<DeviceRegisterResult>("/api/v1/devices/register", {
    method: "POST",
    body: { device_id: deviceId, expo_push_token: expoPushToken, platform },
  });
}

export function unregisterDevice(deviceId: string): Promise<void> {
  return apiRequest<void>(`/api/v1/devices/${deviceId}`, { method: "DELETE" });
}
