import { resolve } from "node:path";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Multi-page build: the voice app at /web/ (index.html, DEC-38) plus the debug pages at their
// old URLs (/web/chat.html, /web/mic-test.html).
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
        "mic-test": resolve(import.meta.dirname, "mic-test.html"),
      },
    },
  },
  test: {
    include: ["src/**/*.test.ts"], // unit tests; browser tests live in e2e/ (Playwright)
  },
  server: {
    proxy: {
      "/chat": backend,
      "/debug": backend,
      "/businesses": backend,
      "/voice": { target: backend, ws: true },
    },
  },
});
