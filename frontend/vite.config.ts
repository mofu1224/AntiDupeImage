import { timingSafeEqual } from "node:crypto";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import type { Plugin } from "vite";

const launchHealthPath = "/_antidupeimage/launch-health";
const launchNonceHeader = "x-antidupeimage-launch-nonce";

function nonceMatches(supplied: string, expected: string): boolean {
  const suppliedBytes = Buffer.from(supplied);
  const expectedBytes = Buffer.from(expected);
  return suppliedBytes.length === expectedBytes.length && timingSafeEqual(suppliedBytes, expectedBytes);
}

function launchOwnershipPlugin(): Plugin {
  return {
    name: "antidupeimage-launch-ownership",
    configureServer(server) {
      server.middlewares.use((request, response, next) => {
        if (request.url !== launchHealthPath) {
          next();
          return;
        }

        const expected = process.env.ANTIDUPEIMAGE_LAUNCH_NONCE ?? "";
        const header = request.headers[launchNonceHeader];
        const supplied = Array.isArray(header) ? header[0] : (header ?? "");
        response.setHeader("Cache-Control", "no-store");
        response.setHeader("Content-Type", "application/json; charset=utf-8");
        if (!expected || !supplied || !nonceMatches(supplied, expected)) {
          response.statusCode = 404;
          response.end(JSON.stringify({ detail: "Not Found" }));
          return;
        }

        response.statusCode = 200;
        response.end(JSON.stringify({ nonce: expected }));
      });
    }
  };
}

export default defineConfig({
  plugins: [react(), launchOwnershipPlugin()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    allowedHosts: ["127.0.0.1", "localhost"],
    cors: false
  },
  cacheDir: "../.vite-runtime",
  test: {
    globals: true,
    environment: "jsdom",
    include: ["src/**/*.{test,spec}.{ts,tsx}", "e2e/**/*.test.ts"],
    setupFiles: "./src/test/setup.ts"
  }
});
