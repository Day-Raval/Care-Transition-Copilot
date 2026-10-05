import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { execFileSync } from "node:child_process";

function wslApiTarget() {
  if (process.platform !== "win32") return null;
  try {
    const ip = execFileSync("wsl", ["sh", "-lc", "hostname -I | awk '{print $1}'"], {
      encoding: "utf8",
      timeout: 2000,
    }).trim();
    return ip ? `http://${ip}:8080` : null;
  } catch {
    return null;
  }
}

const apiTarget = process.platform === "win32"
  ? process.env.API_PROXY_TARGET || wslApiTarget() || "http://127.0.0.1:8080"
  : "http://127.0.0.1:8080";

export default defineConfig({
  plugins: [react()],
  envDir: "../",
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: apiTarget,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
