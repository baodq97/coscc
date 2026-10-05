import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const studio = fileURLToPath(new URL("./src", import.meta.url));
const features = fileURLToPath(new URL("../coscc/features", import.meta.url));

// The studio is served by the app at `/` (see `coscc/http/studio.py`) and built into the
// package, so a wheel carries it like the rest of `coscc/`. A feature's `ui/` sits in its own
// folder under `coscc/features/`; it imports the studio as `@studio/...` and react from here.
export default defineConfig({
  base: "/",
  plugins: [react()],
  resolve: { alias: { "@studio": studio }, dedupe: ["react", "react-dom"] },
  build: { outDir: "../coscc/_studio", emptyOutDir: true },
  server: {
    fs: { allow: [studio, features, fileURLToPath(new URL(".", import.meta.url))] },
    proxy: { "/api": "http://127.0.0.1:8790", "/login": "http://127.0.0.1:8790" },
  },
});
