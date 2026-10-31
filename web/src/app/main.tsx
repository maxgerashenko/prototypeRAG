import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./app.css";

// scrollbars are invisible until the element scrolls (see app.css): mark it while it moves
const scrollTimers = new WeakMap<Element, number>();
document.addEventListener(
  "scroll",
  (e) => {
    const el = e.target;
    if (!(el instanceof Element)) return;
    el.classList.add("is-scrolling");
    window.clearTimeout(scrollTimers.get(el));
    scrollTimers.set(el, window.setTimeout(() => el.classList.remove("is-scrolling"), 800));
  },
  { capture: true, passive: true },
);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

// Installable app: the service worker caches the app shell only (web/public/sw.js). Production
// build only — in `npm run dev` it would cache Vite's modules and hide edits.
if (import.meta.env.PROD && "serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/web/sw.js", { scope: "/web/" }).catch(() => {});
  });
}
