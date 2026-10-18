import { resolve } from "node:path";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Multi-page build: the voice app at /web/ (index.html, DEC-42) plus debug pages:
// /web/chat.html, /web/voice-debug.html (open mic + raw event log) and /web/call.html
// (Twilio Voice SDK call, mode B). /web/mic-test.html,
// the voice app's old URL, redirects to /web/ (app/main.py).
// FastAPI serves web/dist under /web (app/main.py); `npm run dev` serves the same pages on
// :5173 and proxies the API and the voice WebSocket to uvicorn on :8000.
const backend = "http://localhost:8000";

export default defineConfig({
  base: "/web/",
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    rollupOptions: {
      input: {
        app: resolve(import.meta.dirname, "index.html"),
        chat: resolve(import.meta.dirname, "chat.html"),
        "voice-debug": resolve(import.meta.dirname, "voice-debug.html"),
        call: resolve(import.meta.dirname, "call.html"),
      },
    },
  },
  test: {
    include: ["src/**/*.test.ts"], // unit tests; browser tests live in e2e/ (Playwright)
  },
  server: {
    proxy: {
      "/chat": backend,
      "/health": backend,
      "/dev": backend,
      "/debug": backend,
      "/businesses": backend,
      "/twilio": backend,
      "/voice": { target: backend, ws: true },
    },
  },
});
