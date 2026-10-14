import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const apiTarget = process.env.API_PROXY_TARGET || "http://127.0.0.1:8080";

export default defineConfig(({ mode }) => {
  const local = mode === "deployment";
  return {
  plugins: [react(), ...(local ? [{
    name: "local-runtime-config",
    transformIndexHtml: () => [{ tag: "script", attrs: { src: "/local-config.js" }, injectTo: "head-prepend" }],
  }] : [])],
  envDir: "../",
  define: local ? {
    "import.meta.env.VITE_API_BASE_URL": JSON.stringify("/api"),
    "import.meta.env.VITE_API_KEY": JSON.stringify(""),
    "import.meta.env.VITE_AUTH_MODE": JSON.stringify("api_key"),
  } : {},
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
  };
});
