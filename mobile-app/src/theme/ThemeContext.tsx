import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import * as SecureStore from "expo-secure-store";

const THEME_KEY = "omniflow_theme_mode";

export type ThemeMode = "dark" | "light";

export type ThemeColors = {
  mode: ThemeMode;
  screenBg: string;
  cardBg: string;
  border: string;
  textPrimary: string;
  textSecondary: string;
  textMuted: string;
  teal: string;
  indigo: string;
  tabBarBg: string;
  iconButtonBg: string;
};

const DARK: ThemeColors = {
  mode: "dark",
  screenBg: "#0b0f1a",
  cardBg: "#111827",
  border: "rgba(255,255,255,0.08)",
  textPrimary: "#f1f5f9",
  textSecondary: "#94a3b8",
  textMuted: "#64748b",
  teal: "#2DD4BF",
  indigo: "#6657F2",
  tabBarBg: "rgba(11,15,26,0.97)",
  iconButtonBg: "#111827",
};

const LIGHT: ThemeColors = {
  mode: "light",
  screenBg: "#eef1f6",
  cardBg: "#ffffff",
  border: "rgba(11,17,32,0.08)",
  textPrimary: "#0B1120",
  textSecondary: "#475569",
  textMuted: "#94a3b8",
  teal: "#0d9488",
  indigo: "#6657F2",
  tabBarBg: "rgba(255,255,255,0.97)",
  iconButtonBg: "#f4f7fb",
};

type ThemeContextValue = {
  colors: ThemeColors;
  toggleTheme: () => void;
};

const ThemeContext = createContext<ThemeContextValue>({ colors: DARK, toggleTheme: () => {} });

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<ThemeMode>("dark");

  useEffect(() => {
    SecureStore.getItemAsync(THEME_KEY).then((saved) => {
      if (saved === "light" || saved === "dark") setMode(saved);
    });
  }, []);

  const toggleTheme = () => {
    setMode((prev) => {
      const next = prev === "dark" ? "light" : "dark";
      SecureStore.setItemAsync(THEME_KEY, next).catch(() => {});
      return next;
    });
  };

  const value = useMemo(() => ({ colors: mode === "dark" ? DARK : LIGHT, toggleTheme }), [mode]);

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  return useContext(ThemeContext);
}
