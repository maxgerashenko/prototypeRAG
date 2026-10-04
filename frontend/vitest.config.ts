import { defineConfig } from "vitest/config";

// Pure-function tests only (date/duration labels, initials, host parsing, PCM
// conversion) -- no DOM needed, so the default "node" environment is enough.
export default defineConfig({
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
