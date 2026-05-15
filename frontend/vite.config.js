import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/ws": {
        target: "ws://localhost:8000",
        ws: true,
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on("error", () => {}); // backend not up yet — handled in UI
        },
      },
      "/health": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
});
