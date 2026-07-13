import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { I18nProvider, getStoredLocale } from "./hooks/useI18n";
import "./styles/app.css";

const storedTheme = localStorage.getItem("agentforge-theme");
document.documentElement.setAttribute(
  "data-theme",
  storedTheme === "light" ? "light" : "dark",
);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <I18nProvider initialLocale={getStoredLocale()}>
      <App />
    </I18nProvider>
  </StrictMode>,
);
