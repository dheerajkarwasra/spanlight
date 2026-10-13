import { fileURLToPath, URL } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const backendTarget = process.env.VITE_BACKEND_URL ?? "http://localhost:8000";

// `/gw` is the gateway: the proxy streams bodies through unbuffered and adds no compression.
const proxiedPaths = ["/api", "/v1", "/gw", "/health"];

const FONT_FILE = /\.(woff2?|ttf|otf)$/;

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: Object.fromEntries(
      proxiedPaths.map((path) => [path, { target: backendTarget, changeOrigin: false }]),
    ),
  },
  build: {
    sourcemap: true,
    // Never inline fonts as data: URLs: the production CSP allows fonts from 'self' only.
    assetsInlineLimit: (filePath) => (FONT_FILE.test(filePath) ? false : undefined),
    rolldownOptions: {
      output: {
        // Long-lived vendor chunks cache across deploys; app code changes more often.
        codeSplitting: {
          groups: [
            { name: "react", test: /node_modules[\\/](react|react-dom|scheduler)[\\/]/ },
            { name: "tanstack", test: /node_modules[\\/]@tanstack[\\/]/ },
            { name: "radix", test: /node_modules[\\/](@radix-ui|radix-ui)[\\/]/ },
            { name: "charts", test: /node_modules[\\/](recharts|d3-[a-z-]+|victory-vendor)[\\/]/ },
            // zod and its jitless switch share a chunk, so the switch runs before any chunk that
            // builds a schema at import time. In the entry chunk it ran after them, and zod's
            // `new Function` probe broke the CSP on every page load.
            { name: "zod", test: /node_modules[\\/]zod[\\/]|src[\\/]lib[\\/]zod-config\.ts$/ },
          ],
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
  },
});
