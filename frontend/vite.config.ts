import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Mounted at /web by app/main.py (StaticFiles over frontend/dist) — URLs stay
// /web/mic-test.html and /web/chat.html (DEC-41).
export default defineConfig({
  base: "/web/",
  plugins: [react()],
  build: {
    outDir: "dist",
    rollupOptions: {
      input: {
        micTest: new URL("./mic-test.html", import.meta.url).pathname,
        chat: new URL("./chat.html", import.meta.url).pathname,
      },
    },
  },
  server: {
    // Dev server proxies the API + the /voice WebSocket to uvicorn (replaces the old
    // SSE live-reload / DEV_RELOAD — Vite's own HMR covers that job in dev).
    proxy: {
      // covers GET /businesses, /businesses/{id}/conversations[/...], and
      // /businesses/{id}/custom-replies (nested under the same prefix).
      "/businesses": "http://localhost:8000",
      "/chat": "http://localhost:8000",
      "/debug": "http://localhost:8000",
      "/health": "http://localhost:8000",
      "/voice": { target: "ws://localhost:8000", ws: true },
    },
  },
});
