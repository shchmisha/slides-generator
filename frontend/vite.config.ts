import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [vue()],
  server: {
    // Local dev without Docker: the API runs on :8000, same-origin /api like in nginx.
    proxy: { "/api": "http://localhost:8000" },
  },
  test: {
    environment: "jsdom",
  },
});
