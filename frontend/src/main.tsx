import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { AuthGate, AuthProvider } from "./auth";
import "./styles.css";
import "./auth.css";
import "./theme.css";
import { initializeTheme } from "./ThemeToggle";

initializeTheme();

const rootElement = document.getElementById("root");

if (!rootElement) {
  throw new Error("Missing root element");
}

createRoot(rootElement).render(
  <StrictMode>
    <AuthProvider>
      <AuthGate />
    </AuthProvider>
  </StrictMode>,
);
