import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Built files are served by the voice changer server at /panel/.
// In `npm run dev`, API calls are proxied to the running server. Only the dev server's own
// Origin is dropped (so the server treats the panel as same-origin); any other Origin is
// forwarded untouched, so the server's trusted-origin check still rejects other websites.
const DEV_PORT = 5173;
const DEV_ORIGINS = new Set([`http://localhost:${DEV_PORT}`, `http://127.0.0.1:${DEV_PORT}`, `http://[::1]:${DEV_PORT}`]);

export default defineConfig({
  base: "/panel/",
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: DEV_PORT,
    strictPort: true,
    proxy: {
      "/vt": {
        target: "http://127.0.0.1:18888",
        configure: (proxy) => {
          proxy.on("proxyReq", (proxyReq, req) => {
            const origin = req.headers.origin;
            if (origin && DEV_ORIGINS.has(origin)) proxyReq.removeHeader("origin");
          });
        },
      },
    },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
