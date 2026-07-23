// Every timestamp in OmniFlow must display as IST, regardless of the
// device's own timezone setting — using `undefined` locale/timezone in
// toLocaleString etc. silently falls back to whatever timezone the phone
// is configured with, which usually happens to be IST for an India-based
// deployment but isn't guaranteed (misconfigured device, travel, testing
// from a different locale). Every formatter here pins "Asia/Kolkata"
// explicitly so display is always correct, matching the website's `|ist`
// Jinja filter (app/templates_env.py).
const IST_TZ = "Asia/Kolkata";

export function formatIstTime(iso: string | null | undefined): string {
  if (!iso) return "--:--";
  return new Date(iso).toLocaleTimeString("en-IN", {
    timeZone: IST_TZ,
    hour: "numeric",
    minute: "2-digit",
  });
}

// "20 Jul, 6:00 PM" — used for ticket/checklist due dates & timestamps so
// they always read in IST, matching the desktop's `|ist` Jinja filter
// output, regardless of the device's own timezone.
export function formatIstDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-IN", {
    timeZone: IST_TZ,
    day: "numeric",
    month: "short",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
}

export function formatIstDate(d: Date): string {
  return d.toLocaleDateString("en-IN", {
    timeZone: IST_TZ,
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

export function formatIstDateWithWeekday(d: Date): string {
  return d.toLocaleDateString("en-IN", {
    timeZone: IST_TZ,
    weekday: "short",
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

// YYYY-MM-DD as seen in IST — NOT the same as d.toISOString().slice(0, 10),
// which converts to UTC first and can land on the wrong calendar day within
// ~5.5 hours of midnight IST.
export function toIstIsoDate(d: Date): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: IST_TZ,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(d);
}

export function getIstHour(d: Date = new Date()): number {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: IST_TZ,
    hour: "numeric",
    hourCycle: "h23",
  }).formatToParts(d);
  const hourPart = parts.find((p) => p.type === "hour");
  return hourPart ? parseInt(hourPart.value, 10) : d.getHours();
}

// Current year/month (1-12) as seen in IST, for defaulting the attendance
// calendar — matters only within ~5.5 hours of midnight IST, but should
// still be correct rather than silently off by a month at that boundary.
export function getIstYearMonth(d: Date = new Date()): { year: number; month: number } {
  const iso = toIstIsoDate(d); // YYYY-MM-DD
  return { year: parseInt(iso.slice(0, 4), 10), month: parseInt(iso.slice(5, 7), 10) };
}
