import { fileURLToPath, URL } from "node:url";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    port: 5173,
    strictPort: true,
  },
  build: {
    // pywebview loads the built UI from disk via the engine, so keep paths relative.
    outDir: "dist",
    // A desktop app loading from local disk: one 700 kB bundle costs nothing worth splitting.
    chunkSizeWarningLimit: 1500,
    emptyOutDir: true,
  },
});
