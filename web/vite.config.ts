import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: {
    // Same origin in dev and in compose (nginx): the browser calls /api/..., the prefix is stripped, no CORS.
    proxy: { "/api": { target: "http://localhost:8000", rewrite: (path) => path.replace(/^\/api/, "") } },
  },
  test: { environment: "jsdom", include: ["tests/**/*.test.ts?(x)"], globals: false },
});
