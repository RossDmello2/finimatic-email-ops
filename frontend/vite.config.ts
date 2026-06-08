import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "FINIMATIC_");
  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        "/api": {
          target: env.FINIMATIC_DEV_API_TARGET || "http://127.0.0.1:8000",
          changeOrigin: true
        }
      }
    }
  };
});
