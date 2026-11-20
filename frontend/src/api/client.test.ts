import { beforeEach, expect, test, vi } from "vitest";
import {
  bootstrapApiToken,
  chooseFolder,
  fetchThumbnail,
  getApiToken,
  getGroup,
  getGroupSummaries,
  listScans,
  loadInitialToken,
  resetAppData,
  setApiToken
} from "./client";

beforeEach(() => {
  window.history.replaceState(null, "", "/");
  setApiToken("");
  vi.restoreAllMocks();
});

test("portable API URLs resolve to the current origin without persisting credentials", async () => {
  vi.stubEnv("VITE_API_BASE", "/api");
  vi.resetModules();
  try {
    const portable = await import("./client");
    expect(portable.API_BASE).toBe(`${window.location.origin}/api`);
    portable.setApiToken("test-memory-token");
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ scans: [], total: 0 }), { status: 200 })
    );
    await portable.listScans();
    expect(fetchMock.mock.calls[0][0]).toBe(`${window.location.origin}/api/scans?limit=50&offset=0`);
    expect(window.location.href).not.toContain("test-memory-token");
  } finally {
    vi.unstubAllEnvs();
    vi.resetModules();
  }
});

test("production builds use the serving origin without an API override", async () => {
  vi.stubEnv("PROD", true);
  vi.stubEnv("VITE_API_BASE", undefined);
  vi.resetModules();
  try {
    const production = await import("./client");
    expect(production.API_BASE).toBe(`${window.location.origin}/api`);
    window.history.replaceState(null, "", "/#bootstrap=production-capability");
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ api_token: "production-test-token" }), { status: 200 })
    );
    await production.bootstrapApiToken();
    expect(fetchMock.mock.calls[0][0]).toBe(`${window.location.origin}/_antidupeimage/bootstrap`);
    expect(window.location.hash).toBe("");
  } finally {
    vi.unstubAllEnvs();
    vi.resetModules();
  }
});

test("loadInitialToken consumes the fragment token without persisting it in history state", () => {
  window.history.replaceState(
    { workspace: "exact" },
    "",
    "/review?tab=exact#token=fragment-secret&view=grid"
  );

  const token = loadInitialToken();

  expect(token).toBe("fragment-secret");
  expect(loadInitialToken()).toBe("");
  expect(window.location.pathname).toBe("/review");
  expect(window.location.search).toBe("?tab=exact");
  expect(window.location.hash).toBe("#view=grid");
  expect(window.history.state.workspace).toBe("exact");
  expect(window.history.state).not.toHaveProperty("__antidupeimage_api_token");
});

test("setApiToken stays in memory without changing the URL or history state", () => {
  window.history.replaceState({ workspace: "scan" }, "", "/review?tab=scan#details");

  setApiToken(" manual-secret ");

  expect(getApiToken()).toBe("manual-secret");
  expect(loadInitialToken()).toBe("");
  expect(window.location.pathname).toBe("/review");
  expect(window.location.search).toBe("?tab=scan");
  expect(window.location.hash).toBe("#details");
  expect(window.history.state).toEqual({ workspace: "scan" });

  setApiToken("");
  expect(loadInitialToken()).toBe("");
  expect(window.history.state).toEqual({ workspace: "scan" });
});

test("bootstrapApiToken exchanges and removes a one-time fragment before API use", async () => {
  window.history.replaceState(
    { workspace: "scan" },
    "",
    "/review#bootstrap=one-time-capability&view=grid"
  );
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ api_token: "persistent-sentinel" }), {
      status: 200,
      headers: { "Content-Type": "application/json", "Cache-Control": "no-store" }
    })
  );

  await expect(bootstrapApiToken()).resolves.toBe(true);

  expect(getApiToken()).toBe("persistent-sentinel");
  expect(window.location.hash).toBe("#view=grid");
  expect(window.location.href).not.toContain("persistent-sentinel");
  expect(fetchMock).toHaveBeenCalledWith("http://127.0.0.1:8000/_antidupeimage/bootstrap", {
    method: "POST",
    headers: { "X-AntiDupeImage-Bootstrap": "one-time-capability" }
  });
});

test("chooseFolder sends the token header and initial path", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ path: "/tmp/images", cancelled: false }), {
      status: 200,
      headers: { "Content-Type": "application/json" }
    })
  );
  setApiToken("secret-token");

  const response = await chooseFolder(" /tmp ");

  expect(response.path).toBe("/tmp/images");
  expect(fetchMock).toHaveBeenCalledWith(
    "http://127.0.0.1:8000/api/dialogs/folder",
    {
      method: "POST",
      body: JSON.stringify({ initial_path: "/tmp" }),
      headers: {
        "Content-Type": "application/json",
        "X-AntiDupeImage-Token": "secret-token"
      }
    }
  );
});

test("fetchThumbnail sends the local API token as a header", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response("image", {
      status: 200,
      headers: { "Content-Type": "image/jpeg" }
    })
  );
  setApiToken("secret-token");

  const blob = await fetchThumbnail(7);

  expect(blob.type).toBe("image/jpeg");
  expect(fetchMock).toHaveBeenCalledWith(
    "http://127.0.0.1:8000/api/images/7/thumbnail",
    {
      headers: {
        "X-AntiDupeImage-Token": "secret-token"
      }
    }
  );
});

test("getGroupSummaries sends paging filter and sort query parameters", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(
      JSON.stringify({
        scan_id: 3,
        groups: [],
        total: 0,
        limit: 25,
        offset: 50,
        filter: "visual",
        sort: "files"
      }),
      {
        status: 200,
        headers: { "Content-Type": "application/json" }
      }
    )
  );
  setApiToken("secret-token");

  await getGroupSummaries(3, 25, 50, "visual", "files");

  expect(fetchMock).toHaveBeenCalledWith(
    "http://127.0.0.1:8000/api/scans/3/group-summaries?limit=25&offset=50&filter=visual&sort=files",
    {
      headers: {
        "Content-Type": "application/json",
        "X-AntiDupeImage-Token": "secret-token"
      }
    }
  );
});

test("getGroup requests one group detail", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(
      JSON.stringify({
        id: 7,
        score: 1,
        reason: "exact-pixel",
        recommended_keep_id: 70,
        images: []
      }),
      {
        status: 200,
        headers: { "Content-Type": "application/json" }
      }
    )
  );
  setApiToken("secret-token");

  await getGroup(3, 7);

  expect(fetchMock).toHaveBeenCalledWith(
    "http://127.0.0.1:8000/api/scans/3/groups/7",
    {
      headers: {
        "Content-Type": "application/json",
        "X-AntiDupeImage-Token": "secret-token"
      }
    }
  );
});

test("listScans adds an optional server-side status filter", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ scans: [], total: 0, limit: 20, offset: 0 }), {
      status: 200,
      headers: { "Content-Type": "application/json" }
    })
  );
  setApiToken("secret-token");

  await listScans(20, 40, "failed");

  expect(fetchMock).toHaveBeenCalledWith(
    "http://127.0.0.1:8000/api/scans?limit=20&offset=40&status=failed",
    {
      headers: {
        "Content-Type": "application/json",
        "X-AntiDupeImage-Token": "secret-token"
      }
    }
  );
});

test("resetAppData sends explicit confirmation with the token header", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(
      JSON.stringify({
        scans_deleted: 1,
        scan_images_deleted: 2,
        groups_deleted: 1,
        group_members_deleted: 2,
        failures_deleted: 0,
        cached_images_deleted: 2
      }),
      {
        status: 200,
        headers: { "Content-Type": "application/json" }
      }
    )
  );
  setApiToken("secret-token");

  const response = await resetAppData();

  expect(response.scans_deleted).toBe(1);
  expect(fetchMock).toHaveBeenCalledWith(
    "http://127.0.0.1:8000/api/system/reset",
    {
      method: "POST",
      body: JSON.stringify({ confirm: true }),
      headers: {
        "Content-Type": "application/json",
        "X-AntiDupeImage-Token": "secret-token"
      }
    }
  );
});
