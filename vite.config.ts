import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => ({
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    watch: {
      ignored: [
        /[\\/]src-tauri[\\/]target[\\/]/,
        /[\\/]\.venv[\\/]/,
        /[\\/]\.local-app-data[\\/]/
      ]
    }
  },
  envPrefix: ["VITE_", "TAURI_ENV_"],
  build: { target: "chrome105", minify: mode === "development" ? false : "esbuild", sourcemap: mode === "development" },
  test: { environment: "jsdom", setupFiles: ["./src/test/setup.ts"] }
}));
