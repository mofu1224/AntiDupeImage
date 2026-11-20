/// <reference types="node" />

import { defineConfig, devices } from "@playwright/test";
import { randomBytes } from "node:crypto";
import { chmodSync, existsSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

const reuseExistingServers = process.env.ANTIDUPEIMAGE_E2E_REUSE_SERVERS === "1";
const configuredApiToken = process.env.ANTIDUPEIMAGE_E2E_TOKEN?.trim() || undefined;
if (reuseExistingServers && !configuredApiToken) {
  throw new Error(
    "ANTIDUPEIMAGE_E2E_TOKEN must match the disposable backend when reusing E2E servers"
  );
}
const apiToken = configuredApiToken ?? randomBytes(32).toString("base64url");
process.env.ANTIDUPEIMAGE_E2E_TOKEN = apiToken;
if (reuseExistingServers) {
  process.env.ANTIDUPEIMAGE_E2E_OWNS_HANDOFF = "0";
} else {
  const bootstrapToken =
    process.env.ANTIDUPEIMAGE_E2E_BOOTSTRAP?.trim() || randomBytes(32).toString("base64url");
  process.env.ANTIDUPEIMAGE_E2E_BOOTSTRAP = bootstrapToken;

  const configuredHandoffPath = process.env.ANTIDUPEIMAGE_E2E_HANDOFF?.trim() || undefined;
  const handoffPath = configuredHandoffPath ?? createHandoffFile(bootstrapToken);
  process.env.ANTIDUPEIMAGE_E2E_HANDOFF = handoffPath;
  process.env.ANTIDUPEIMAGE_E2E_OWNS_HANDOFF = configuredHandoffPath ? "0" : "1";
}
const localChromiumExecutable = findAvailableChromiumExecutable();

export default defineConfig({
  testDir: "./e2e",
  outputDir: "../.playwright-runtime/e2e",
  globalSetup: "./e2e/global-setup.ts",
  testMatch: "**/*.spec.ts",
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI
    ? [
        ["dot"],
        ["html", { open: "never", outputFolder: "playwright-report" }]
      ]
    : "list",
  use: {
    baseURL: "http://127.0.0.1:5173",
    trace: "retain-on-failure"
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        ...(localChromiumExecutable ? { launchOptions: { executablePath: localChromiumExecutable } } : {}),
      }
    }
  ]
});

function createHandoffFile(bootstrap: string): string {
  const directory = mkdtempSync(path.join(tmpdir(), "antidupeimage-handoff."));
  chmodSync(directory, 0o700);
  const handoff = path.join(directory, "handoff.html");
  writeFileSync(
    handoff,
    `<meta name="antidupeimage-bootstrap" content="${bootstrap}">`,
    { encoding: "utf8", mode: 0o600 }
  );
  chmodSync(handoff, 0o600);
  return handoff;
}

function findAvailableChromiumExecutable(): string | undefined {
  if (process.platform !== "win32") {
    return undefined;
  }
  const candidates = [
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    "C:/Program Files/Microsoft/Edge/Application/msedge.exe"
  ];
  return candidates.find((candidate) => existsSync(candidate));
}
