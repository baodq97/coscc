import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The studio is served by the app at `/` (see `coscc/studio.py`) and built into the
// package, so a wheel carries it like the rest of `coscc/`.
export default defineConfig({
  base: "/",
  plugins: [react()],
  build: { outDir: "../coscc/_studio", emptyOutDir: true },
  server: { proxy: { "/api": "http://127.0.0.1:8790", "/login": "http://127.0.0.1:8790" } },
});
