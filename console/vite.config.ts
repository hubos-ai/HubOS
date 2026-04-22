import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  // Empty = same-origin; frontend and backend served together, no hardcoded host.
  // Use a dedicated Vite-prefixed key so unrelated shell BASE_URL values don't leak into the build.
  const apiBaseUrl = env.VITE_API_BASE_URL ?? "";
  const devAuthBypass = env.VITE_DEV_AUTH_BYPASS === "true";

  return {
    define: {
      VITE_API_BASE_URL: JSON.stringify(apiBaseUrl),
      TOKEN: JSON.stringify(env.TOKEN || ""),
      MOBILE: false,
      VITE_DEV_AUTH_BYPASS: devAuthBypass,
    },
    plugins: [react()],
    css: {
      modules: {
        localsConvention: "camelCase",
        generateScopedName: "[name]__[local]__[hash:base64:5]",
      },
      preprocessorOptions: {
        less: {
          javascriptEnabled: true,
        },
      },
    },
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "./src"),
        "@ant-design/x": path.resolve(__dirname, "./src/shims/antDesignX.tsx"),
      },
    },
    server: {
      host: "0.0.0.0",
      port: 5173,
      // strictPort prevents Vite from silently jumping to 5174/5175 when 5173
      // is busy. Old browser tabs would otherwise keep hitting a stale dev
      // server on the original port and load a stale bundle.
      strictPort: true,
      proxy: {
        // Proxy /api requests to XClaw backend during development.
        // This avoids CORS preflight issues when the browser makes direct
        // cross-origin requests to http://localhost:8001.
        "/api": {
          target: "http://localhost:8001",
          changeOrigin: true,
          // XClaw backend expects /api prefix on all routes
          rewrite: (path: string) => path,
        },
      },
    },
    optimizeDeps: {
      include: ["diff"],
    },
    // build: {
    //   // Output to HubOS's console directory,
    //   // so we don't need to copy files manually after build.
    //   outDir: path.resolve(__dirname, "../src/hubos/console"),
    //   emptyOutDir: true,
    // },
  };
});
