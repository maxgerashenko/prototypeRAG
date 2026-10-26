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
