// Static per-role navigation shortcuts, mirroring the design's TILES/FULL_NAV
// tables and the desktop site's bottom_nav.html role conditionals. These are
// not API data — role comes from the logged-in user, the destinations are
// fixed. Only screens in BUILT_SCREENS actually exist today; everything else
// taps through to a "not built yet" notice instead of a dead/blank screen.

import { getIstHour } from "../utils/dateFormat";

export type NavCategory = "op" | "crm" | "sales" | "org" | "setup";

export const CATEGORY_COLOR: Record<NavCategory, { bg: string; fg: string }> = {
  op: { bg: "rgba(45,212,191,0.14)", fg: "#2DD4BF" },
  crm: { bg: "rgba(102,87,242,0.14)", fg: "#6657F2" },
  sales: { bg: "rgba(59,130,246,0.14)", fg: "#3b82f6" },
  org: { bg: "rgba(234,179,8,0.14)", fg: "#eab308" },
  setup: { bg: "rgba(148,163,184,0.16)", fg: "#64748b" },
};

export type NavId =
  | "home" | "tasks" | "tickets" | "fms" | "checklists" | "training"
  | "inventory" | "customers" | "sell" | "askai" | "attendance" | "org" | "setup";

export const NAV_DEF: Record<NavId, { icon: string; label: string }> = {
  home: { icon: "🏠", label: "Home" },
  tasks: { icon: "📋", label: "My Tasks" },
  tickets: { icon: "🎫", label: "Tickets" },
  fms: { icon: "🔀", label: "Flow Board" },
  checklists: { icon: "✅", label: "Checklists" },
  training: { icon: "🎓", label: "Training" },
  inventory: { icon: "📦", label: "Inventory" },
  customers: { icon: "🧑‍💼", label: "Customers" },
  sell: { icon: "🧾", label: "Sell" },
  askai: { icon: "✦", label: "Ask AI" },
  attendance: { icon: "📍", label: "Attendance" },
  org: { icon: "👥", label: "Organization" },
  setup: { icon: "⚙️", label: "Setup" },
};

export const FULL_NAV: Record<string, NavId[]> = {
  ADMIN: ["home", "tasks", "attendance", "tickets", "checklists", "fms", "training", "inventory", "customers", "sell", "askai"],
  MANAGER: ["home", "tasks", "attendance", "tickets", "checklists", "fms", "training", "inventory", "customers", "sell"],
  EMPLOYEE: ["home", "tasks", "attendance", "tickets", "checklists", "training"],
  PRODUCT_MANAGER: ["home", "org", "setup"],
};

export const BOTTOM_COUNT: Record<string, number> = { ADMIN: 4, MANAGER: 4, EMPLOYEE: 4, PRODUCT_MANAGER: 3 };

export type Tile = { icon: string; title: string; sub: string; cat: NavCategory; nav: NavId };

export const TILES: Record<string, Tile[]> = {
  ADMIN: [
    { icon: "📋", title: "My Tasks", sub: "Your personal action queue", cat: "op", nav: "tasks" },
    { icon: "🧭", title: "Operations", sub: "Dashboard, Tickets, Checklists & Flow Board", cat: "op", nav: "tickets" },
    { icon: "🤝", title: "CRM", sub: "Customers, contacts & accounts", cat: "crm", nav: "customers" },
    { icon: "📦", title: "Sales & Inventory", sub: "Stock, orders & sales insights", cat: "sales", nav: "inventory" },
    { icon: "👥", title: "Organization", sub: "Employees, Training, Attendance & Leave", cat: "org", nav: "org" },
    { icon: "⚙️", title: "Setup", sub: "Configuration & preferences", cat: "setup", nav: "setup" },
  ],
  MANAGER: [
    { icon: "📋", title: "My Tasks", sub: "Your personal action queue", cat: "op", nav: "tasks" },
    { icon: "🧭", title: "Operations", sub: "Dashboard, Tickets, Checklists & Flow Board", cat: "op", nav: "tickets" },
    { icon: "🤝", title: "CRM", sub: "Customers, contacts & accounts", cat: "crm", nav: "customers" },
    { icon: "📦", title: "Sales & Inventory", sub: "Stock, orders & sales insights", cat: "sales", nav: "inventory" },
    { icon: "👥", title: "Organization", sub: "Employees, Training, Attendance & Leave", cat: "org", nav: "org" },
  ],
  EMPLOYEE: [
    { icon: "🏠", title: "Dashboard", sub: "Your daily overview", cat: "op", nav: "home" },
    { icon: "📋", title: "My Tasks", sub: "Your personal action queue", cat: "op", nav: "tasks" },
    { icon: "📍", title: "Attendance", sub: "Punch in/out & leave", cat: "op", nav: "attendance" },
    { icon: "🎫", title: "Tickets", sub: "Track & resolve tickets", cat: "op", nav: "tickets" },
    { icon: "✅", title: "Checklists", sub: "Your assigned checklists", cat: "op", nav: "checklists" },
    { icon: "🎓", title: "Training", sub: "Knowledge & materials", cat: "org", nav: "training" },
  ],
  PRODUCT_MANAGER: [
    { icon: "👥", title: "Organization", sub: "Employees & training", cat: "org", nav: "org" },
    { icon: "⚙️", title: "Setup", sub: "Configuration & preferences", cat: "setup", nav: "setup" },
  ],
};

export const GREETING_SUB: Record<string, string> = {
  ADMIN: "Here's what's moving across the floor today.",
  MANAGER: "Here's how your team is tracking today.",
  EMPLOYEE: "Here's your queue for today.",
  PRODUCT_MANAGER: "Configuration and workforce setup, in one place.",
};

// The only screens that actually exist in the app today.
export const BUILT_SCREENS: Partial<Record<NavId, keyof import("../navigation/AuthNavigator").AuthStackParamList>> = {
  home: "Home",
  attendance: "Attendance",
  tickets: "Tickets",
  setup: "Setup",
};

// Maps each nav destination to the Setup > Access Control tab key
// (app/constants.py TAB_CATALOG) that gates it, mirroring the website's own
// nav. Destinations not in this map (home, tasks/My Tasks, org, setup,
// askai) aren't feature-gated on the backend either, so they're never
// hidden by enabled_tabs.
const NAV_TAB_KEY: Partial<Record<NavId, string>> = {
  tickets: "TICKETS",
  checklists: "CHECKLISTS",
  fms: "FMS",
  training: "KNOWLEDGE",
  inventory: "INVENTORY",
  customers: "SALES",
  sell: "SALES",
  attendance: "ATTENDANCE",
};

// True if this nav destination should be shown at all, per the tenant's
// Setup-configured enabled_tabs (GET /api/v1/home). Ungated destinations
// (see NAV_TAB_KEY above) are always shown.
export function isNavEnabled(navId: NavId, enabledTabs: string[]): boolean {
  const tabKey = NAV_TAB_KEY[navId];
  if (!tabKey) return true;
  return enabledTabs.includes(tabKey);
}

export function timeOfDayGreeting(): string {
  const h = getIstHour();
  if (h < 12) return "Good morning";
  if (h < 17) return "Good afternoon";
  return "Good evening";
}
