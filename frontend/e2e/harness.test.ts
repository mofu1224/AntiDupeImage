// @vitest-environment node

import { buildOwnedBackendEnvironment, stripApplicationEnvironment } from "./harness";

test("owned backend environment removes inherited application settings", () => {
  const environment = buildOwnedBackendEnvironment({
    parentEnvironment: {
      PATH: "keep-me",
      ANTIDUPEIMAGE_DB: "parent.sqlite3",
      antidupeimage_home: "parent-home",
    },
    apiToken: "owned-token",
    appHome: "owned-home",
    handoffPath: "owned-handoff",
    launchNonce: "owned-nonce",
    uiOrigin: "http://127.0.0.1:5173",
    uvCacheDir: "owned-cache",
    pythonEnvironment: { VIRTUAL_ENV: "owned-venv" },
  });

  expect(environment.PATH).toBe("keep-me");
  expect(environment.ANTIDUPEIMAGE_DB).toBeUndefined();
  expect(environment.antidupeimage_home).toBeUndefined();
  expect(environment.ANTIDUPEIMAGE_HOME).toBe("owned-home");
  expect(environment.ANTIDUPEIMAGE_API_TOKEN).toBe("owned-token");
});

test("frontend child environment also excludes application settings", () => {
  const environment = stripApplicationEnvironment({
    ANTIDUPEIMAGE_API_TOKEN: "parent-token",
    VITE_API_BASE: "parent-api",
    PATH: "keep-me",
  });

  expect(environment.ANTIDUPEIMAGE_API_TOKEN).toBeUndefined();
  expect(environment.VITE_API_BASE).toBe("parent-api");
  expect(environment.PATH).toBe("keep-me");
});
