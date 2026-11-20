import type { FullConfig } from "@playwright/test";
import { spawn, type ChildProcess } from "node:child_process";
import { randomBytes } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { lstat, rm } from "node:fs/promises";
import { connect } from "node:net";
import { tmpdir } from "node:os";
import path from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";
import { buildOwnedBackendEnvironment, stripApplicationEnvironment } from "./harness";

const API_URL = "http://127.0.0.1:8000/api/openapi.json";
const API_SYSTEM_URL = "http://127.0.0.1:8000/api/system";
const UI_URL = "http://127.0.0.1:5173";
const STARTUP_TIMEOUT_MS = 120_000;
const GRACEFUL_STOP_TIMEOUT_MS = 5_000;
const FORCE_STOP_TIMEOUT_MS = 2_000;
const childErrors = new WeakMap<ChildProcess, Error>();

export default async function globalSetup(_config: FullConfig): Promise<(() => Promise<void>) | void> {
  const reuseExistingServers = process.env.ANTIDUPEIMAGE_E2E_REUSE_SERVERS === "1";
  const apiToken = requiredEnvironment("ANTIDUPEIMAGE_E2E_TOKEN");
  const ownedHandoffPath =
    process.env.ANTIDUPEIMAGE_E2E_OWNS_HANDOFF === "1"
      ? requiredEnvironment("ANTIDUPEIMAGE_E2E_HANDOFF")
      : undefined;
  const [apiAvailable, uiAvailable, apiPortUsed, uiPortUsed] = await Promise.all([
    isUrlAvailable(API_URL),
    isUrlAvailable(UI_URL),
    isPortUsed(8000),
    isPortUsed(5173)
  ]);

  if (reuseExistingServers) {
    if (!apiAvailable || !uiAvailable) {
      throw new Error(
        "ANTIDUPEIMAGE_E2E_REUSE_SERVERS=1 requires both http://127.0.0.1:8000 and " +
          "http://127.0.0.1:5173 to be ready"
      );
    }
    await verifyReusableBackend(apiToken);
    return;
  }

  if (apiPortUsed || uiPortUsed) {
    const occupied = [apiPortUsed ? "127.0.0.1:8000" : "", uiPortUsed ? "127.0.0.1:5173" : ""]
      .filter(Boolean)
      .join(" and ");
    const occupiedError = new Error(
      `${occupied} is already in use; stop it or explicitly opt into disposable E2E server reuse`
    );
    if (ownedHandoffPath) {
      try {
        await removeOwnedTempDirectory(path.dirname(ownedHandoffPath), "antidupeimage-handoff.");
      } catch (cleanupError) {
        throw new AggregateError(
          [occupiedError, cleanupError],
          "E2E ports are occupied and generated handoff cleanup failed"
        );
      }
    }
    throw occupiedError;
  }

  const setupDir = path.dirname(fileURLToPath(import.meta.url));
  const frontendDir = path.resolve(setupDir, "..");
  const repoRoot = path.resolve(frontendDir, "..");
  const backendDir = path.join(repoRoot, "backend");
  const e2eHome = path.join(
    tmpdir(),
    `antidupeimage-e2e-home-${process.pid}-${randomBytes(6).toString("hex")}`
  );
  const handoffPath = requiredEnvironment("ANTIDUPEIMAGE_E2E_HANDOFF");
  const startupAbort = new AbortController();
  const abortStartup = () => startupAbort.abort(new Error("E2E server startup was interrupted"));
  const startupSignals: NodeJS.Signals[] = ["SIGINT", "SIGTERM", "SIGHUP"];
  for (const signal of startupSignals) process.once(signal, abortStartup);

  let backend: ChildProcess | undefined;
  let frontend: ChildProcess | undefined;
  try {
    backend = startBackend(repoRoot, backendDir, e2eHome, handoffPath, apiToken);
    await waitForUrl(API_URL, "backend", backend, startupAbort.signal);
    frontend = startFrontend(frontendDir);
    await waitForUrl(UI_URL, "frontend", frontend, startupAbort.signal);
  } catch (error) {
    try {
      await cleanupOwnedRuntime(frontend, backend, e2eHome, ownedHandoffPath);
    } catch (cleanupError) {
      throw new AggregateError([error, cleanupError], "E2E startup and owned-process cleanup both failed");
    }
    throw error;
  } finally {
    for (const signal of startupSignals) process.off(signal, abortStartup);
  }

  return async () => {
    await cleanupOwnedRuntime(frontend, backend, e2eHome, ownedHandoffPath);
  };
}

function startBackend(
  repoRoot: string,
  backendDir: string,
  e2eHome: string,
  handoffPath: string,
  apiToken: string
): ChildProcess {
  const python = resolvePythonRuntime(repoRoot, backendDir);

  return trackChild(
    spawn(
      python.executable,
      [
        "-m",
        "uvicorn",
        "app.main:app",
        "--app-dir",
        backendDir,
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
        "--no-access-log"
      ],
      {
        cwd: repoRoot,
        env: buildOwnedBackendEnvironment({
          parentEnvironment: process.env,
          apiToken,
          appHome: e2eHome,
          handoffPath,
          launchNonce: randomBytes(32).toString("base64url"),
          uiOrigin: UI_URL,
          uvCacheDir: path.join(repoRoot, ".uv-cache"),
          pythonEnvironment: python.environment
        }),
        shell: false,
        stdio: "inherit",
        windowsHide: true
      },
    )
  );
}

function resolvePythonRuntime(
  repoRoot: string,
  backendDir: string
): { executable: string; environment: Record<string, string> } {
  const pythonRelativePath = process.platform === "win32" ? "Scripts/python.exe" : "bin/python";
  const virtualEnvironment = [
    process.env.UV_PROJECT_ENVIRONMENT ? path.resolve(repoRoot, process.env.UV_PROJECT_ENVIRONMENT) : undefined,
    path.join(repoRoot, ".venv"),
    path.join(backendDir, ".venv")
  ].find(
    (candidate): candidate is string => Boolean(candidate && existsSync(path.join(candidate, pythonRelativePath)))
  );
  if (!virtualEnvironment) {
    throw new Error(
      "No project Python virtual environment was found; run `uv sync --project backend --locked --extra dev`"
    );
  }
  if (process.platform !== "win32") {
    return {
      executable: path.join(virtualEnvironment, pythonRelativePath),
      environment: { VIRTUAL_ENV: virtualEnvironment }
    };
  }

  const configuration = readFileSync(path.join(virtualEnvironment, "pyvenv.cfg"), "utf8");
  const baseHome = /^home\s*=\s*(.+)$/m.exec(configuration)?.[1]?.trim();
  const basePython = baseHome ? path.join(baseHome, "python.exe") : "";
  if (!basePython || !existsSync(basePython)) {
    throw new Error(`Could not resolve the base Python executable for ${virtualEnvironment}`);
  }
  const sitePackages = path.join(virtualEnvironment, "Lib", "site-packages");
  const pythonPath = process.env.PYTHONPATH
    ? `${sitePackages}${path.delimiter}${process.env.PYTHONPATH}`
    : sitePackages;
  return {
    // uv-managed Windows venv launchers create a child process and then exit.
    // Starting the resolved interpreter keeps the owned PID equal to the server PID.
    executable: basePython,
    environment: {
      PYTHONPATH: pythonPath,
      VIRTUAL_ENV: virtualEnvironment
    }
  };
}

function startFrontend(frontendDir: string): ChildProcess {
  const viteScript = path.join(frontendDir, "node_modules", "vite", "bin", "vite.js");
  if (!existsSync(viteScript)) {
    throw new Error("Vite is not installed; run `npm ci --prefix frontend`");
  }
  return trackChild(
    spawn(
      process.execPath,
      [
        viteScript,
        "--configLoader",
        "runner",
        "--host",
        "127.0.0.1",
        "--port",
        "5173",
        "--strictPort"
      ],
      {
        cwd: frontendDir,
        env: {
          ...stripApplicationEnvironment(process.env),
          VITE_API_BASE: "http://127.0.0.1:8000/api"
        },
        shell: false,
        stdio: "inherit",
        windowsHide: true
      }
    )
  );
}

async function waitForUrl(
  url: string,
  name: string,
  child: ChildProcess,
  startupSignal: AbortSignal
): Promise<void> {
  const deadline = Date.now() + STARTUP_TIMEOUT_MS;
  while (Date.now() < deadline) {
    startupSignal.throwIfAborted();
    const childError = childErrors.get(child);
    if (childError) throw childError;
    if (hasExited(child)) {
      throw new Error(
        `${name} exited before becoming ready (${child.signalCode ?? `code ${child.exitCode}`})`
      );
    }
    if (await isUrlAvailable(url)) return;
    startupSignal.throwIfAborted();
    await delay(100);
  }
  throw new Error(`Timed out waiting for ${name} at ${url}`);
}

async function isUrlAvailable(url: string): Promise<boolean> {
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(1_000) });
    await response.body?.cancel();
    return response.ok;
  } catch {
    return false;
  }
}

function isPortUsed(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = connect({ host: "127.0.0.1", port });
    const finish = (used: boolean) => {
      socket.destroy();
      resolve(used);
    };
    socket.once("connect", () => finish(true));
    socket.once("error", () => finish(false));
    socket.setTimeout(1_000, () => finish(false));
  });
}

async function verifyReusableBackend(apiToken: string): Promise<void> {
  let response: Response;
  try {
    response = await fetch(API_SYSTEM_URL, {
      headers: { "X-AntiDupeImage-Token": apiToken },
      signal: AbortSignal.timeout(2_000)
    });
  } catch (error) {
    throw new Error("Could not verify the reusable E2E backend", { cause: error });
  }
  await response.body?.cancel();
  if (!response.ok) {
    throw new Error(
      `Reusable E2E backend rejected ANTIDUPEIMAGE_E2E_TOKEN (HTTP ${response.status})`
    );
  }
}

async function cleanupOwnedRuntime(
  frontend: ChildProcess | undefined,
  backend: ChildProcess | undefined,
  e2eHome: string,
  ownedHandoffPath: string | undefined
): Promise<void> {
  const errors: unknown[] = [];
  for (const [name, child] of [
    ["frontend", frontend],
    ["backend", backend]
  ] as const) {
    try {
      await stopOwnedProcess(name, child);
    } catch (error) {
      errors.push(error);
    }
  }
  if (errors.length) {
    throw new AggregateError(errors, "One or more owned E2E server processes could not be stopped");
  }

  await removeOwnedTempDirectory(e2eHome, "antidupeimage-e2e-home-");
  if (ownedHandoffPath) {
    await removeOwnedTempDirectory(path.dirname(ownedHandoffPath), "antidupeimage-handoff.");
  }
}

async function stopOwnedProcess(name: string, child: ChildProcess | undefined): Promise<void> {
  if (!child || hasExited(child)) return;
  child.kill("SIGTERM");
  if (await waitForExit(child, GRACEFUL_STOP_TIMEOUT_MS)) return;

  let forceError: unknown;
  try {
    child.kill("SIGKILL");
  } catch (error) {
    forceError = error;
  }
  if (await waitForExit(child, FORCE_STOP_TIMEOUT_MS)) return;
  throw new Error(`Owned ${name} process ${child.pid ?? "unknown"} did not exit`, {
    cause: forceError ?? childErrors.get(child)
  });
}

function trackChild(child: ChildProcess): ChildProcess {
  child.on("error", (error) => childErrors.set(child, error));
  return child;
}

function waitForExit(child: ChildProcess, timeoutMs: number): Promise<boolean> {
  if (hasExited(child)) return Promise.resolve(true);
  return new Promise((resolve) => {
    const onExit = () => {
      clearTimeout(timer);
      resolve(true);
    };
    const timer = setTimeout(() => {
      child.off("exit", onExit);
      resolve(hasExited(child));
    }, timeoutMs);
    child.once("exit", onExit);
  });
}

function hasExited(child: ChildProcess): boolean {
  return child.exitCode !== null || child.signalCode !== null;
}

async function removeOwnedTempDirectory(directory: string, expectedPrefix: string): Promise<void> {
  const resolvedTempRoot = path.resolve(tmpdir());
  const resolvedDirectory = path.resolve(directory);
  if (
    path.dirname(resolvedDirectory) !== resolvedTempRoot ||
    !path.basename(resolvedDirectory).startsWith(expectedPrefix)
  ) {
    throw new Error(`Refusing to remove unexpected E2E temporary directory: ${resolvedDirectory}`);
  }
  try {
    const directoryStat = await lstat(resolvedDirectory);
    if (!directoryStat.isDirectory() || directoryStat.isSymbolicLink()) {
      throw new Error(`Refusing to recursively remove non-directory E2E path: ${resolvedDirectory}`);
    }
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return;
    throw error;
  }
  await rm(resolvedDirectory, { force: true, recursive: true, maxRetries: 2 });
}

function requiredEnvironment(name: string): string {
  const value = process.env[name]?.trim();
  if (!value) throw new Error(`${name} is required for Playwright E2E`);
  return value;
}
