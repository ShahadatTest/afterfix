import { useState } from "react";

type Theme = "light" | "dark";
const STORAGE_KEY = "afterfix-theme";

export function initializeTheme(): void {
  let theme: Theme = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved === "light" || saved === "dark") theme = saved;
  } catch { /* A blocked preference store must not prevent sign-in. */ }
  document.documentElement.dataset.theme = theme;
}

export default function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(() => document.documentElement.dataset.theme === "dark" ? "dark" : "light");
  const toggle = () => {
    const next: Theme = theme === "light" ? "dark" : "light";
    document.documentElement.dataset.theme = next;
    setTheme(next);
    try { window.localStorage.setItem(STORAGE_KEY, next); } catch { /* Session preference still works. */ }
  };
  return <button type="button" className="theme-toggle" role="switch" aria-checked={theme === "dark"} aria-label="Dark theme" onClick={toggle} title={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}>
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true">
      {theme === "dark" ? <path d="M20.4 14.2A8.6 8.6 0 0 1 9.8 3.6a8.7 8.7 0 1 0 10.6 10.6Z" /> : <><circle cx="12" cy="12" r="3.7" /><path d="M12 2v2m0 16v2M2 12h2m16 0h2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></>}
    </svg>
    <span>{theme === "dark" ? "Dark" : "Light"}</span>
  </button>;
}
