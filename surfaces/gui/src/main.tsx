import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";
import { I18nProvider } from "./i18n";
import { initTheme } from "./theme";
import { isTauri, platformOS } from "./tauri";
import "./tailwind.css";
import "./styles.css";

initTheme();
// Platform hook for CSS (html[data-platform="windows"] scrollbar styling etc.).
document.documentElement.dataset.platform = platformOS();

// Offline shell for production browser builds only. The desktop shell loads local assets
// over its own protocol (no SW there), and dev must never cache Vite's transformed module
// graph — a stale cache would hide edits until the next SW bump.
if (import.meta.env.PROD && !isTauri() && "serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register(`${import.meta.env.BASE_URL}sw.js`).catch(() => {
      /* offline support is optional; never block boot on it */
    });
  });
}

// A file dropped OUTSIDE a drop target (the composer) must never navigate the webview to the
// file itself — the browser/WKWebView default. Drop targets stopPropagation-free preventDefault
// in their own handlers; these guards only catch the misses. (The desktop shell disables Tauri's
// native drag-drop interception so HTML5 drag events reach the DOM at all — see lib.rs.)
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("drop", (e) => e.preventDefault());

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <I18nProvider>
      <App />
    </I18nProvider>
  </React.StrictMode>,
);
