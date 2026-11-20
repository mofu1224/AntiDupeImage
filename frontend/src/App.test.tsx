import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { vi } from "vitest";
import App from "./App";
import type {
  DuplicateGroup,
  GroupSummary,
  ImageSummary,
  ScanState,
  SystemResponse
} from "./types/api";

const mocks = vi.hoisted(() => ({
  getSystem: vi.fn(),
  listScans: vi.fn(),
  chooseFolder: vi.fn(),
  createScan: vi.fn(),
  getScan: vi.fn(),
  getGroupSummaries: vi.fn(),
  getGroup: vi.fn(),
  getGroups: vi.fn(),
  getFailures: vi.fn(),
  cancelScan: vi.fn(),
  deleteScan: vi.fn(),
  moveDuplicateFiles: vi.fn(),
  resetAppData: vi.fn(),
  fetchThumbnail: vi.fn(),
  setApiToken: vi.fn(),
  sources: [] as FakeEventSource[]
}));

class FakeEventSource {
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;

  close() {
    this.closed = true;
  }
}

vi.mock("./api/client", () => ({
  ApiError: class extends Error {
    status: number;

    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
  getApiToken: () => "test-token",
  setApiToken: mocks.setApiToken,
  getSystem: mocks.getSystem,
  listScans: mocks.listScans,
  chooseFolder: mocks.chooseFolder,
  createScan: mocks.createScan,
  getScan: mocks.getScan,
  getGroupSummaries: mocks.getGroupSummaries,
  getGroup: mocks.getGroup,
  getGroups: mocks.getGroups,
  getFailures: mocks.getFailures,
  cancelScan: mocks.cancelScan,
  deleteScan: mocks.deleteScan,
  moveDuplicateFiles: mocks.moveDuplicateFiles,
  resetAppData: mocks.resetAppData,
  fetchThumbnail: mocks.fetchThumbnail,
  scanEventSource: () => {
    const source = new FakeEventSource();
    mocks.sources.push(source);
    return source;
  }
}));

const system: SystemResponse = {
  python: "3.12",
  platform: "test",
  app_dir: "/tmp/app",
  db_path: "/tmp/app/db.sqlite3",
  device: {
    selected: "cpu",
    available: ["cpu"],
    cuda_devices: [],
    mps_available: false,
    torch_available: false,
    torch_version: null
  },
  clip: {
    available: false,
    version: null,
    error: "not installed"
  },
  default_model: "ViT-B-32",
  default_pretrained: "pretrained",
  token_required: true
};

function scanState(status: ScanState["status"], id = 1): ScanState {
  return {
    id,
    status,
    phase: status === "completed" ? "completed" : "queued",
    progress_detail: status === "completed" ? "Completed with 0 duplicate group(s)" : "Waiting to start",
    path: "/tmp/images",
    recursive: true,
    model: "ViT-B-32",
    pretrained: "pretrained",
    device: "cpu",
    thresholds: {
      phash_distance: 12,
      histogram_similarity: 0.88,
      clip_similarity: 0.92,
      clip_enabled: false,
      precision_mode: "conservative",
      performance_profile: "standard"
    },
    progress: status === "completed" ? 1 : 0,
    total_files: 1,
    processed_files: status === "completed" ? 1 : 0,
    succeeded_files: status === "completed" ? 1 : 0,
    failed_files: 0,
    skipped_files: 0,
    candidate_pairs: 0,
    duplicate_groups: 0,
    error: null,
    warnings: [],
    created_at: "2026-06-03 00:00:00",
    updated_at: "2026-06-03 00:00:00",
    can_cancel: status === "queued" || status === "running"
  };
}

function group(id: number): DuplicateGroup {
  return {
    id,
    score: 1,
    reason: "exact-pixel",
    recommended_keep_id: id * 10,
    images: []
  };
}

function groupSummary(id: number): GroupSummary {
  return {
    id,
    score: 1,
    reason: "exact-pixel",
    recommended_keep_id: id * 10,
    recommended_keep_path: `/tmp/images/${id * 10}.png`,
    image_count: 2,
    moved_count: 0
  };
}

function image(id: number, reason: string): ImageSummary {
  return {
    id,
    path: `/tmp/images/${id}.png`,
    width: 20,
    height: 20,
    pixel_count: 400,
    size_bytes: 100,
    phash: "0:0:0",
    avg_rgb: [0, 0, 0],
    score: 1,
    reason,
    is_recommended_keep: reason === "recommended-keep",
    moved: false,
    trashed: false,
    status: "available",
    available: true,
    evidence: null
  };
}

function duplicateMoveGroup(): DuplicateGroup {
  return {
    id: 1,
    score: 1,
    reason: "exact-pixel",
    recommended_keep_id: 10,
    images: [image(10, "recommended-keep"), image(11, "exact-pixel")]
  };
}

beforeEach(() => {
  vi.resetAllMocks();
  window.localStorage.removeItem("antidupeimage.layout.v1");
  mocks.sources.length = 0;
  mocks.getSystem.mockResolvedValue(system);
  mocks.listScans.mockResolvedValue({ scans: [], total: 0, limit: 50, offset: 0 });
  mocks.chooseFolder.mockResolvedValue({ path: "/tmp/images", cancelled: false });
  mocks.getGroupSummaries.mockResolvedValue({
    scan_id: 1,
    groups: [],
    total: 0,
    limit: 100,
    offset: 0,
    filter: "all",
    sort: "recommended"
  });
  mocks.getGroup.mockImplementation((_scanId: number, groupId: number) => Promise.resolve(group(groupId)));
  mocks.getGroups.mockResolvedValue({ scan_id: 1, groups: [], total: 0, limit: 100, offset: 0 });
  mocks.getFailures.mockResolvedValue({ scan_id: 1, failures: [], total: 0, limit: 100, offset: 0 });
  mocks.fetchThumbnail.mockResolvedValue(new Blob(["image"], { type: "image/jpeg" }));
  mocks.resetAppData.mockResolvedValue({
    scans_deleted: 0,
    scan_images_deleted: 0,
    groups_deleted: 0,
    group_members_deleted: 0,
    failures_deleted: 0,
    cached_images_deleted: 0
  });
});

async function openWorkspaceTab(name: RegExp | string) {
  fireEvent.click(await screen.findByRole("tab", { name }));
}

async function openScanTab() {
  await openWorkspaceTab(/Scan/);
}

async function openDuplicatesTab() {
  await openWorkspaceTab(/Duplicates/);
}

async function openWarningsTab() {
  await openWorkspaceTab(/Warnings/);
}

test("workspace resize handles adjust and persist layout dimensions", async () => {
  render(<App />);

  await openDuplicatesTab();
  const reviewHandle = screen.getByRole("separator", { name: "Resize compare inspector" });
  const reviewHeightHandle = screen.getByRole("separator", { name: "Resize review area" });
  const workspace = document.querySelector(".ledger-workspace") as HTMLElement;
  const reviewSurface = document.querySelector(".review-surface") as HTMLElement;
  const reviewRect = vi.spyOn(reviewSurface, "getBoundingClientRect").mockReturnValue({
    x: 460,
    y: 120,
    width: 700,
    height: 620,
    top: 120,
    right: 1160,
    bottom: 740,
    left: 460,
    toJSON: () => ({})
  });

  fireEvent.keyDown(reviewHandle, { key: "ArrowLeft" });
  expect(workspace.style.getPropertyValue("--compare-panel-width")).toBe("540px");
  fireEvent.keyDown(reviewHeightHandle, { key: "ArrowUp" });
  expect(workspace.style.getPropertyValue("--review-surface-height")).toBe("588px");

  await act(async () => {
    fireEvent.pointerDown(reviewHandle, { pointerId: 1, clientX: 640 });
  });
  const dragMove = new Event("pointermove") as PointerEvent;
  Object.defineProperty(dragMove, "clientX", { value: 600 });
  await act(async () => {
    window.dispatchEvent(dragMove);
  });
  fireEvent.pointerUp(window);

  expect(workspace.style.getPropertyValue("--compare-panel-width")).toBe("560px");

  await act(async () => {
    fireEvent.pointerDown(reviewHeightHandle, { pointerId: 2, clientY: 740 });
  });
  const dragHeight = new Event("pointermove") as PointerEvent;
  Object.defineProperty(dragHeight, "clientY", { value: 860 });
  await act(async () => {
    window.dispatchEvent(dragHeight);
  });
  fireEvent.pointerUp(window);

  expect(workspace.style.getPropertyValue("--review-surface-height")).toBe("740px");
  await waitFor(() => {
    expect(JSON.parse(window.localStorage.getItem("antidupeimage.layout.v1") ?? "{}")).toMatchObject({
      comparePanel: 560,
      reviewSurface: 740
    });
  });
  reviewRect.mockRestore();
});

test("workspace tabs switch duplicate review, exact matches, and warnings panels", async () => {
  const exactGroup = groupSummary(1);
  const visualGroup = { ...groupSummary(2), reason: "visual-hash", score: 0.92 };
  const firstFailure = {
    id: 1,
    path: "/tmp/images/problem.png",
    stage: "decode",
    error: "failed",
    created_at: "2026-06-03 00:00:00"
  };
  mocks.listScans.mockResolvedValue({
    scans: [{ ...scanState("completed"), warnings: ["CLIP unavailable"] }],
    total: 1,
    limit: 50,
    offset: 0
  });
  mocks.getGroupSummaries.mockImplementation(
    (_scanId: number, _limit: number, offset: number, filter: string, sort: string) => {
      const groups = filter === "exact" ? [exactGroup] : [exactGroup, visualGroup];
      return Promise.resolve({
        scan_id: 1,
        groups,
        total: groups.length,
        limit: 100,
        offset,
        filter,
        sort
      });
    }
  );
  mocks.getFailures.mockResolvedValue({
    scan_id: 1,
    failures: [firstFailure],
    total: 1,
    limit: 100,
    offset: 0
  });
  render(<App />);

  await openDuplicatesTab();
  expect(await screen.findByText("Group 1")).toBeInTheDocument();
  expect(screen.getByText("Group 2")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("tab", { name: /Exact matches/ }));
  expect(screen.getByLabelText("Group filter")).toHaveValue("exact");
  await waitFor(() => expect(screen.queryByText("Group 2")).not.toBeInTheDocument());
  expect(screen.getByText("Group 1")).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: /Exact matches/ })).toHaveAttribute("aria-selected", "true");

  fireEvent.click(screen.getByRole("tab", { name: /Duplicates/ }));
  expect(screen.getByLabelText("Group filter")).toHaveValue("all");
  expect(await screen.findByText("Group 2")).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: /Duplicates/ })).toHaveAttribute("aria-selected", "true");

  fireEvent.click(screen.getByRole("tab", { name: /Warnings/ }));
  const warningsTab = screen.getByRole("tab", { name: /Warnings/ });
  expect(warningsTab).toHaveAttribute("aria-selected", "true");
  expect(screen.getByText("/tmp/images/problem.png")).toBeInTheDocument();

  fireEvent.keyDown(warningsTab, { key: "ArrowRight" });
  const overviewTab = screen.getByRole("tab", { name: "Overview" });
  await waitFor(() => expect(overviewTab).toHaveFocus());
  expect(overviewTab).toHaveAttribute("aria-selected", "true");

  fireEvent.keyDown(overviewTab, { key: "End" });
  await waitFor(() => expect(warningsTab).toHaveFocus());
  expect(warningsTab).toHaveAttribute("aria-selected", "true");
});

test("completed scan group load failure is reported without leaving submission disabled", async () => {
  mocks.createScan.mockResolvedValue({ id: 1, status: "queued" });
  mocks.getScan.mockResolvedValue(scanState("queued"));
  mocks.getGroupSummaries.mockRejectedValue(new Error("groups failed"));
  render(<App />);

  fireEvent.click(screen.getByRole("button", { name: "Choose folder" }));
  await waitFor(() => expect(screen.getByLabelText("Image or folder path")).toHaveValue("/tmp/images"));
  fireEvent.click(screen.getByRole("button", { name: "Start scan" }));

  await waitFor(() => expect(mocks.sources).toHaveLength(1));
  await act(async () => {
    mocks.sources[0].onmessage?.({ data: JSON.stringify(scanState("completed")) } as MessageEvent);
  });

  expect(await screen.findAllByText("groups failed")).toHaveLength(1);
  expect(screen.getByRole("button", { name: "Start scan" })).not.toBeDisabled();
});

test("loads stored scan settings for review without starting automatically", async () => {
  const historical = {
    ...scanState("completed", 7),
    path: "/tmp/archive-photos",
    recursive: false,
    model: "ViT-L-14",
    pretrained: "stored-profile",
    device: "cuda:2",
    thresholds: {
      phash_distance: 6,
      histogram_similarity: 0.96,
      clip_similarity: 0.98,
      clip_enabled: false,
      precision_mode: "ultra_strict" as const,
      performance_profile: "lightweight" as const
    }
  };
  mocks.listScans.mockResolvedValue({
    scans: [historical],
    total: 1,
    limit: 50,
    offset: 0
  });
  mocks.getSystem.mockResolvedValue({
    ...system,
    device: {
      ...system.device,
      available: ["cpu", "cuda:2"],
      cuda_devices: ["cuda:2"]
    }
  });
  mocks.createScan.mockResolvedValue({ id: 8, status: "queued" });
  mocks.getScan.mockResolvedValue(scanState("queued", 8));
  render(<App />);

  fireEvent.click(screen.getByRole("button", { name: "Advanced settings" }));
  fireEvent.click(screen.getByLabelText("Fallback if CLIP fails"));
  expect(screen.getByLabelText("Fallback if CLIP fails")).not.toBeChecked();
  fireEvent.click(await screen.findByRole("button", { name: "Use settings from scan 7" }));

  await waitFor(() =>
    expect(screen.getByLabelText("Image or folder path")).toHaveValue("/tmp/archive-photos")
  );
  expect(screen.getByLabelText("Include subfolders")).not.toBeChecked();
  expect(screen.getByLabelText("Model")).toHaveValue("ViT-L-14");
  expect(screen.getByLabelText("Pretrained")).toHaveValue("stored-profile");
  expect(screen.getByLabelText("Device")).toHaveValue("cuda");
  expect(screen.getByLabelText("GPU index")).toHaveValue(2);
  expect(screen.getByLabelText("Performance profile")).toHaveValue("lightweight");
  expect(screen.getByLabelText("Precision mode")).toHaveValue("ultra_strict");
  expect(screen.getByLabelText("pHash distance value")).toHaveValue(6);
  expect(
    screen.getAllByText(
      "Settings from scan #7 loaded."
    )
  ).toHaveLength(1);
  expect(screen.getByLabelText("Fallback if CLIP fails")).not.toBeChecked();
  expect(mocks.createScan).not.toHaveBeenCalled();

  fireEvent.change(screen.getByLabelText("Model"), { target: { value: "temporary-edit" } });
  fireEvent.click(screen.getByRole("button", { name: "Use settings from scan 7" }));
  await waitFor(() => expect(screen.getByLabelText("Model")).toHaveValue("ViT-L-14"));

  fireEvent.click(screen.getByRole("button", { name: "Start scan" }));
  await waitFor(() =>
    expect(mocks.createScan).toHaveBeenCalledWith(
      expect.objectContaining({
        path: "/tmp/archive-photos",
        recursive: false,
        model: "ViT-L-14",
        pretrained: "stored-profile",
        device: "cuda",
        gpu_index: 2,
        allow_clip_fallback: false,
        thresholds: expect.objectContaining({
          phash_distance: 6,
          histogram_similarity: 0.96,
          clip_similarity: 0.98,
          precision_mode: "ultra_strict",
          performance_profile: "lightweight"
        })
      })
    )
  );
  await waitFor(() =>
    expect(
      screen.queryByText(
        "Settings from scan #7 loaded."
      )
    ).not.toBeInTheDocument()
  );
});

test("validates a reused GPU only after system capabilities load and falls back explicitly", async () => {
  let resolveSystem: (value: SystemResponse) => void = () => undefined;
  const pendingSystem = new Promise<SystemResponse>((resolve) => {
    resolveSystem = resolve;
  });
  const historical = {
    ...scanState("completed", 9),
    device: "cuda:2"
  };
  mocks.getSystem.mockReturnValue(pendingSystem);
  mocks.listScans.mockResolvedValue({
    scans: [historical],
    total: 1,
    limit: 50,
    offset: 0
  });
  mocks.createScan.mockResolvedValue({ id: 10, status: "queued" });
  mocks.getScan.mockResolvedValue(scanState("queued", 10));
  render(<App />);

  fireEvent.click(await screen.findByRole("button", { name: "Use settings from scan 9" }));
  await waitFor(() => expect(screen.getByLabelText("Device")).toHaveValue("cuda"));
  expect(screen.getByLabelText("GPU index")).toHaveValue(2);
  expect(screen.getByRole("button", { name: "Start scan" })).toBeDisabled();
  expect(screen.getByRole("status")).toHaveTextContent(
    "Device availability will be checked when system status loads."
  );

  await act(async () => {
    resolveSystem({
      ...system,
      device: {
        ...system.device,
        available: ["cpu", "cuda:0"],
        cuda_devices: ["Test GPU 0"]
      }
    });
    await pendingSystem;
  });

  await waitFor(() => expect(screen.getByLabelText("Device")).toHaveValue("auto"));
  expect(screen.getByLabelText("GPU index")).toHaveValue(null);
  expect(screen.getByRole("status")).toHaveTextContent(
    "Device cuda:2 is unavailable on this system, so Auto was selected."
  );

  fireEvent.click(screen.getByRole("button", { name: "Start scan" }));
  await waitFor(() =>
    expect(mocks.createScan).toHaveBeenCalledWith(
      expect.objectContaining({ device: "auto", gpu_index: null })
    )
  );
});

test("late group response from a previous scan does not replace the selected scan", async () => {
  let resolveFirstGroups: (value: unknown) => void = () => undefined;
  const firstGroups = new Promise((resolve) => {
    resolveFirstGroups = resolve;
  });
  mocks.listScans.mockResolvedValue({
    scans: [scanState("completed", 1), scanState("completed", 2)],
    total: 2,
    limit: 50,
    offset: 0
  });
  mocks.getGroupSummaries.mockImplementation((scanId: number) => {
    if (scanId === 1) return firstGroups;
    return Promise.resolve({
      scan_id: 2,
      groups: [groupSummary(2)],
      total: 1,
      limit: 100,
      offset: 0,
      filter: "all",
      sort: "recommended"
    });
  });
  render(<App />);

  await waitFor(() => expect(mocks.getGroupSummaries).toHaveBeenCalledWith(1, 100, 0, "all", "recommended"));
  fireEvent.click(screen.getByText("Scan #2"));
  await openDuplicatesTab();
  expect(await screen.findByText("Group 2")).toBeInTheDocument();

  await act(async () => {
    resolveFirstGroups({
      scan_id: 1,
      groups: [groupSummary(1)],
      total: 1,
      limit: 100,
      offset: 0,
      filter: "all",
      sort: "recommended"
    });
  });

  expect(screen.getByText("Group 2")).toBeInTheDocument();
  expect(screen.queryByText("Group 1")).not.toBeInTheDocument();
});

test("late group page response does not replace a newer page", async () => {
  let resolveFirstNextPage: (value: unknown) => void = () => undefined;
  const firstNextPage = new Promise((resolve) => {
    resolveFirstNextPage = resolve;
  });
  let nextPageCalls = 0;
  mocks.listScans.mockResolvedValue({
    scans: [scanState("completed")],
    total: 1,
    limit: 50,
    offset: 0
  });
  mocks.getGroupSummaries.mockImplementation((_scanId: number, _limit: number, offset: number) => {
    if (offset === 0) {
      return Promise.resolve({
        scan_id: 1,
        groups: [groupSummary(1)],
        total: 101,
        limit: 100,
        offset: 0,
        filter: "all",
        sort: "recommended"
      });
    }
    nextPageCalls += 1;
    if (nextPageCalls === 1) return firstNextPage;
    return Promise.resolve({
      scan_id: 1,
      groups: [groupSummary(3)],
      total: 101,
      limit: 100,
      offset: 100,
      filter: "all",
      sort: "recommended"
    });
  });
  render(<App />);

  await openDuplicatesTab();
  await screen.findByText("Group 1");
  fireEvent.click(screen.getByRole("button", { name: "Next group page" }));
  fireEvent.click(screen.getByRole("button", { name: "Next group page" }));
  expect(await screen.findByText("Group 3")).toBeInTheDocument();

  await act(async () => {
    resolveFirstNextPage({
      scan_id: 1,
      groups: [groupSummary(2)],
      total: 101,
      limit: 100,
      offset: 100,
      filter: "all",
      sort: "recommended"
    });
  });

  expect(screen.getByText("Group 3")).toBeInTheDocument();
  expect(screen.queryByText("Group 2")).not.toBeInTheDocument();
});

test("refreshing scan history reports progress", async () => {
  let resolveRefresh: (value: unknown) => void = () => undefined;
  const refresh = new Promise((resolve) => {
    resolveRefresh = resolve;
  });
  mocks.listScans
    .mockResolvedValueOnce({
      scans: [scanState("completed")],
      total: 1,
      limit: 50,
      offset: 0
    })
    .mockReturnValueOnce(refresh);
  render(<App />);

  await screen.findByText("Scan #1");
  fireEvent.click(screen.getByRole("button", { name: "Refresh scan history" }));

  expect(screen.getByText("Refreshing scan history")).toBeInTheDocument();
  expect(screen.getByText("Requesting latest scan history.")).toBeInTheDocument();
  await act(async () => {
    resolveRefresh({
      scans: [scanState("completed", 2)],
      total: 1,
      limit: 50,
      offset: 0
    });
    await refresh;
  });

  expect(await screen.findByText("Loaded 1 scan(s).")).toBeInTheDocument();
  expect(screen.getByText("Scan #2")).toBeInTheDocument();
});

test("successful scan history refresh clears its stale global error", async () => {
  mocks.listScans
    .mockRejectedValueOnce(new Error("scan history unavailable"))
    .mockResolvedValueOnce({
      scans: [],
      total: 0,
      limit: 50,
      offset: 0
    });
  render(<App />);

  expect(await screen.findByText("scan history unavailable")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Refresh scan history" }));

  await waitFor(() => expect(screen.queryByText("scan history unavailable")).not.toBeInTheDocument());
  expect(await screen.findByText("Loaded 0 scan(s).")).toBeInTheDocument();
});

test("selecting scan reports detail loading progress", async () => {
  mocks.listScans.mockResolvedValue({
    scans: [scanState("completed", 1), scanState("completed", 2)],
    total: 2,
    limit: 50,
    offset: 0
  });
  mocks.getGroupSummaries.mockImplementation((scanId: number) =>
    Promise.resolve({
      scan_id: scanId,
      groups: [groupSummary(scanId)],
      total: 1,
      limit: 100,
      offset: 0,
      filter: "all",
      sort: "recommended"
    })
  );
  render(<App />);

  await screen.findByText("Scan #1");
  fireEvent.click(screen.getByText("Scan #2"));
  await openDuplicatesTab();

  expect(screen.getByText("Loading scan details")).toBeInTheDocument();
  expect(await screen.findByText("Loaded scan #2.")).toBeInTheDocument();
  expect(screen.getByText("Group 2")).toBeInTheDocument();
});

test("completed result CTA opens all duplicate groups and selects the first group", async () => {
  const completed = { ...scanState("completed"), duplicate_groups: 1 };
  mocks.listScans.mockResolvedValue({ scans: [completed], total: 1, limit: 50, offset: 0 });
  mocks.getGroupSummaries.mockResolvedValue({
    scan_id: 1,
    groups: [groupSummary(1)],
    total: 1,
    limit: 100,
    offset: 0,
    filter: "all",
    sort: "recommended"
  });
  render(<App />);

  fireEvent.click(await screen.findByRole("button", { name: "Review duplicates" }));

  expect(screen.getByRole("tab", { name: /Duplicates/ })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByLabelText("Group filter")).toHaveValue("all");
  expect(await screen.findByText("Group 1")).toBeInTheDocument();
  expect(mocks.getGroup).toHaveBeenCalledWith(1, 1);
});

test("completed result CTA opens duplicates in one click after viewing exact matches", async () => {
  const completed = { ...scanState("completed"), duplicate_groups: 1 };
  const exactGroup = groupSummary(1);
  mocks.listScans.mockResolvedValue({ scans: [completed], total: 1, limit: 50, offset: 0 });
  mocks.getGroupSummaries.mockImplementation(
    (_scanId: number, _limit: number, offset: number, filter: string, sort: string) =>
      Promise.resolve({
        scan_id: 1,
        groups: [exactGroup],
        total: 1,
        limit: 100,
        offset,
        filter,
        sort
      })
  );
  render(<App />);

  fireEvent.click(await screen.findByRole("tab", { name: /Exact matches/ }));
  await waitFor(() => expect(screen.getByLabelText("Group filter")).toHaveValue("exact"));
  fireEvent.click(screen.getByRole("tab", { name: "Overview" }));
  fireEvent.click(screen.getByRole("button", { name: "Review duplicates" }));

  expect(screen.getByRole("tab", { name: /Duplicates/ })).toHaveAttribute("aria-selected", "true");
  await waitFor(() => expect(screen.getByLabelText("Group filter")).toHaveValue("all"));
  expect(await screen.findByText("Group 1")).toBeInTheDocument();
});

test("history filtering resets paging and clears an excluded selected scan", async () => {
  const completed = scanState("completed", 1);
  const failed = { ...scanState("failed", 2), phase: "failed" as const, error: "decode failed" };
  mocks.listScans
    .mockResolvedValueOnce({ scans: [completed], total: 1, limit: 50, offset: 0 })
    .mockResolvedValueOnce({ scans: [failed], total: 1, limit: 50, offset: 0 });
  render(<App />);

  await screen.findByText("Scan #1");
  fireEvent.change(screen.getByLabelText("History status"), { target: { value: "failed" } });

  await waitFor(() => expect(mocks.listScans).toHaveBeenLastCalledWith(50, 0, "failed"));
  expect(await screen.findByText("Scan #2")).toBeInTheDocument();
  expect(screen.getByText("待機中")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Review duplicates" })).not.toBeInTheDocument();
});

test("keeps the previous history filter and rows when filtering fails", async () => {
  const completed = scanState("completed", 1);
  mocks.listScans
    .mockResolvedValueOnce({ scans: [completed], total: 1, limit: 50, offset: 0 })
    .mockRejectedValueOnce(new Error("history filter unavailable"));
  render(<App />);

  await screen.findByText("Scan #1");
  fireEvent.change(screen.getByLabelText("History status"), { target: { value: "failed" } });

  expect(await screen.findByText("history filter unavailable")).toBeInTheDocument();
  await waitFor(() => expect(screen.getByLabelText("History status")).toHaveValue("all"));
  expect(screen.getByText("Scan #1")).toBeInTheDocument();
});

test("group pagination reports loading progress", async () => {
  let resolveNextGroups: (value: unknown) => void = () => undefined;
  const nextGroups = new Promise((resolve) => {
    resolveNextGroups = resolve;
  });
  mocks.listScans.mockResolvedValue({
    scans: [scanState("completed")],
    total: 1,
    limit: 50,
    offset: 0
  });
  mocks.getGroupSummaries
    .mockResolvedValueOnce({
      scan_id: 1,
      groups: [groupSummary(1)],
      total: 101,
      limit: 100,
      offset: 0,
      filter: "all",
      sort: "recommended"
    })
    .mockReturnValueOnce(nextGroups);
  render(<App />);

  await openDuplicatesTab();
  await screen.findByText("Group 1");
  fireEvent.click(screen.getByRole("button", { name: "Next group page" }));

  expect(screen.getByText("Loading groups")).toBeInTheDocument();
  expect(screen.getByText("Loading group results from item 101.")).toBeInTheDocument();
  await act(async () => {
    resolveNextGroups({
      scan_id: 1,
      groups: [groupSummary(2)],
      total: 101,
      limit: 100,
      offset: 100,
      filter: "all",
      sort: "recommended"
    });
    await nextGroups;
  });

  expect(await screen.findByText("Loaded 1 group(s).")).toBeInTheDocument();
  expect(screen.getByText("Group 2")).toBeInTheDocument();
});

test("group filter and sort are sent to the summary API", async () => {
  mocks.listScans.mockResolvedValue({
    scans: [scanState("completed")],
    total: 1,
    limit: 50,
    offset: 0
  });
  mocks.getGroupSummaries.mockResolvedValue({
    scan_id: 1,
    groups: [groupSummary(1)],
    total: 1,
    limit: 100,
    offset: 0,
    filter: "all",
    sort: "recommended"
  });
  render(<App />);

  await openDuplicatesTab();
  await screen.findByText("Group 1");
  fireEvent.change(screen.getByLabelText("Group filter"), { target: { value: "visual" } });
  await waitFor(() =>
    expect(mocks.getGroupSummaries).toHaveBeenCalledWith(1, 100, 0, "visual", "recommended")
  );

  fireEvent.change(screen.getByLabelText("Sort groups"), { target: { value: "files" } });
  await waitFor(() =>
    expect(mocks.getGroupSummaries).toHaveBeenCalledWith(1, 100, 0, "visual", "files")
  );
});

test("changing filters clears old rows while the new filter is loading", async () => {
  let resolveFilteredGroups: (value: unknown) => void = () => undefined;
  const filteredGroups = new Promise((resolve) => {
    resolveFilteredGroups = resolve;
  });
  mocks.listScans.mockResolvedValue({
    scans: [{ ...scanState("completed"), duplicate_groups: 2 }],
    total: 1,
    limit: 50,
    offset: 0
  });
  mocks.getGroupSummaries
    .mockResolvedValueOnce({
      scan_id: 1,
      groups: [groupSummary(1)],
      total: 2,
      limit: 100,
      offset: 0,
      filter: "all",
      sort: "recommended"
    })
    .mockReturnValueOnce(filteredGroups);
  render(<App />);

  await openDuplicatesTab();
  expect(await screen.findByText("Group 1")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Group filter"), { target: { value: "visual" } });

  expect(screen.queryByText("Group 1")).not.toBeInTheDocument();
  expect(screen.getByText("Loading duplicate groups")).toBeInTheDocument();
  await act(async () => {
    resolveFilteredGroups({
      scan_id: 1,
      groups: [groupSummary(2)],
      total: 1,
      limit: 100,
      offset: 0,
      filter: "visual",
      sort: "recommended"
    });
    await filteredGroups;
  });

  expect(await screen.findByText("Group 2")).toBeInTheDocument();
});

test("filtered group totals do not replace the selected scan duplicate total", async () => {
  const completed = { ...scanState("completed"), duplicate_groups: 5 };
  mocks.listScans.mockResolvedValue({
    scans: [completed],
    total: 1,
    limit: 50,
    offset: 0
  });
  mocks.getGroupSummaries.mockImplementation(
    (_scanId: number, _limit: number, offset: number, filter: string, sort: string) => {
      const filteredTotal = filter === "visual" ? 2 : 5;
      return Promise.resolve({
        scan_id: 1,
        groups: [groupSummary(1)],
        total: filteredTotal,
        limit: 100,
        offset,
        filter,
        sort
      });
    }
  );
  render(<App />);

  await openDuplicatesTab();
  await screen.findByText("Group 1");
  fireEvent.change(screen.getByLabelText("Group filter"), { target: { value: "visual" } });
  await waitFor(() =>
    expect(mocks.getGroupSummaries).toHaveBeenCalledWith(1, 100, 0, "visual", "recommended")
  );

  expect(screen.getByRole("tab", { name: "Duplicates 5" })).toBeInTheDocument();
  await openWorkspaceTab("Overview");
  const duplicateCard = screen.getByText("Duplicate groups").closest(".summary-card");
  const filteredCard = screen.getByText("Filtered review groups").closest(".summary-card");
  expect(duplicateCard).not.toBeNull();
  expect(filteredCard).not.toBeNull();
  expect(within(duplicateCard as HTMLElement).getByText("5")).toBeInTheDocument();
  expect(within(filteredCard as HTMLElement).getByText("2")).toBeInTheDocument();
});

test("group detail loads after summaries without blocking the progress panel", async () => {
  let resolveDetail: (value: unknown) => void = () => undefined;
  const detail = new Promise((resolve) => {
    resolveDetail = resolve;
  });
  mocks.listScans.mockResolvedValue({
    scans: [scanState("completed")],
    total: 1,
    limit: 50,
    offset: 0
  });
  mocks.getGroupSummaries.mockResolvedValue({
    scan_id: 1,
    groups: [groupSummary(1)],
    total: 1,
    limit: 100,
    offset: 0,
    filter: "all",
    sort: "recommended"
  });
  mocks.getGroup.mockReturnValue(detail);

  render(<App />);

  expect(await screen.findByText("Scan #1 / completed")).toBeInTheDocument();
  await openDuplicatesTab();
  expect(await screen.findByText("Group 1")).toBeInTheDocument();
  expect(screen.getByText("Loading group details")).toBeInTheDocument();

  await act(async () => {
    resolveDetail(duplicateMoveGroup());
    await detail;
  });

  expect(await screen.findByRole("button", { name: "Move to folder" })).toBeInTheDocument();
});

test("group retry does not clear a system-owned error with the same message", async () => {
  mocks.getSystem.mockResolvedValueOnce(system).mockRejectedValueOnce(new Error("Failed to fetch"));
  mocks.listScans.mockResolvedValue({
    scans: [scanState("completed")],
    total: 1,
    limit: 50,
    offset: 0
  });
  mocks.getGroupSummaries
    .mockRejectedValueOnce(new Error("Failed to fetch"))
    .mockResolvedValueOnce({
      scan_id: 1,
      groups: [groupSummary(1)],
      total: 1,
      limit: 100,
      offset: 0,
      filter: "all",
      sort: "recommended"
    });
  render(<App />);

  await openDuplicatesTab();
  expect(await screen.findByRole("alert")).toHaveTextContent("Failed to fetch");
  fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
  await waitFor(() => expect(mocks.getSystem).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(screen.getByRole("button", { name: "Refresh" })).not.toBeDisabled());
  expect(screen.getByRole("alert")).toHaveTextContent("Failed to fetch");
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));

  expect(await screen.findByText("Group 1")).toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent("Failed to fetch");
});

test("successful group detail retry clears its owned global error", async () => {
  mocks.listScans.mockResolvedValue({
    scans: [scanState("completed")],
    total: 1,
    limit: 50,
    offset: 0
  });
  mocks.getGroupSummaries.mockResolvedValue({
    scan_id: 1,
    groups: [groupSummary(1)],
    total: 1,
    limit: 100,
    offset: 0,
    filter: "all",
    sort: "recommended"
  });
  mocks.getGroup
    .mockRejectedValueOnce(new Error("detail failed"))
    .mockResolvedValueOnce(duplicateMoveGroup());
  render(<App />);

  await openDuplicatesTab();
  expect(await screen.findByRole("alert")).toHaveTextContent("detail failed");
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));

  expect(await screen.findByRole("button", { name: "Move to folder" })).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

test("failure pagination reports loading progress", async () => {
  let resolveNextFailures: (value: unknown) => void = () => undefined;
  const nextFailures = new Promise((resolve) => {
    resolveNextFailures = resolve;
  });
  const firstFailure = {
    id: 1,
    path: "/tmp/images/1.png",
    stage: "decode",
    error: "failed",
    created_at: "2026-06-03 00:00:00"
  };
  const secondFailure = { ...firstFailure, id: 2, path: "/tmp/images/2.png" };
  mocks.listScans.mockResolvedValue({
    scans: [scanState("completed")],
    total: 1,
    limit: 50,
    offset: 0
  });
  mocks.getFailures
    .mockResolvedValueOnce({
      scan_id: 1,
      failures: [firstFailure],
      total: 101,
      limit: 100,
      offset: 0
    })
    .mockReturnValueOnce(nextFailures);
  render(<App />);

  await openWarningsTab();
  await screen.findByText("/tmp/images/1.png");
  fireEvent.click(screen.getByRole("button", { name: "Next failure page" }));

  expect(screen.getByText("Loading failures")).toBeInTheDocument();
  expect(screen.getByText("Loading recorded issues from item 101.")).toBeInTheDocument();
  await act(async () => {
    resolveNextFailures({
      scan_id: 1,
      failures: [secondFailure],
      total: 101,
      limit: 100,
      offset: 100
    });
    await nextFailures;
  });

  expect(await screen.findByText("Recorded issues loaded.")).toBeInTheDocument();
  expect(screen.getByText("/tmp/images/2.png")).toBeInTheDocument();
});

test("successful failure retry clears its owned global error", async () => {
  mocks.listScans.mockResolvedValue({
    scans: [scanState("completed")],
    total: 1,
    limit: 50,
    offset: 0
  });
  mocks.getFailures
    .mockRejectedValueOnce(new Error("failures failed"))
    .mockResolvedValueOnce({
      scan_id: 1,
      failures: [],
      total: 0,
      limit: 100,
      offset: 0
    });
  render(<App />);

  await openWarningsTab();
  expect(await screen.findByRole("alert")).toHaveTextContent("failures failed");
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));

  await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  expect(screen.queryByText("failures failed")).not.toBeInTheDocument();
});

test("deleting a previous scan does not clear a newer selection", async () => {
  let resolveDelete: (value: unknown) => void = () => undefined;
  const deletion = new Promise((resolve) => {
    resolveDelete = resolve;
  });
  mocks.listScans.mockResolvedValue({
    scans: [scanState("completed", 1), scanState("completed", 2)],
    total: 2,
    limit: 50,
    offset: 0
  });
  mocks.getGroupSummaries.mockImplementation((scanId: number) =>
    Promise.resolve({
      scan_id: scanId,
      groups: [groupSummary(scanId)],
      total: 1,
      limit: 100,
      offset: 0,
      filter: "all",
      sort: "recommended"
    })
  );
  mocks.deleteScan.mockReturnValue(deletion);
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<App />);

  await screen.findByText("Scan #1");
  fireEvent.click(screen.getByRole("button", { name: "Delete scan 1 history" }));
  fireEvent.click(screen.getByText("Scan #2"));
  await openDuplicatesTab();
  expect(await screen.findByText("Group 2")).toBeInTheDocument();

  await act(async () => {
    resolveDelete({ deleted: true });
  });

  await openScanTab();
  expect(screen.getByText("Scan #2 / completed")).toBeInTheDocument();
  confirm.mockRestore();
});

test("resetting app data clears scan history and selected details", async () => {
  const completed = { ...scanState("completed"), duplicate_groups: 1 };
  mocks.listScans.mockResolvedValue({
    scans: [completed],
    total: 1,
    limit: 50,
    offset: 0
  });
  mocks.getGroupSummaries.mockResolvedValue({
    scan_id: 1,
    groups: [groupSummary(1)],
    total: 1,
    limit: 100,
    offset: 0,
    filter: "all",
    sort: "recommended"
  });
  mocks.resetAppData.mockResolvedValue({
    scans_deleted: 1,
    scan_images_deleted: 2,
    groups_deleted: 1,
    group_members_deleted: 2,
    failures_deleted: 0,
    cached_images_deleted: 2
  });
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<App />);

  await openDuplicatesTab();
  expect(await screen.findByText("Group 1")).toBeInTheDocument();
  await openScanTab();
  fireEvent.click(screen.getByRole("button", { name: "Reset history & cache" }));

  await waitFor(() => expect(mocks.resetAppData).toHaveBeenCalledTimes(1));
  expect(confirm).toHaveBeenCalledWith(
    "Reset all scan history and feature cache? Image files, moved duplicate folders, and the API token will not be deleted."
  );
  expect(screen.queryByText("Scan #1")).not.toBeInTheDocument();
  expect(screen.getByText("No scan history")).toBeInTheDocument();
  await openDuplicatesTab();
  expect(screen.queryByText("Group 1")).not.toBeInTheDocument();
  expect(screen.getByText("No duplicate groups")).toBeInTheDocument();
  expect(
    screen.getAllByText(/Reset app data\. Removed 1 scan\(s\), 2 scan image snapshot\(s\)/)
  ).toHaveLength(2);
  confirm.mockRestore();
});

test("moving files to duplicate folder removes a resolved group and refreshes scan state", async () => {
  const createObjectURL = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:thumb");
  const revokeObjectURL = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
  let resolveMove: (value: unknown) => void = () => undefined;
  const duplicateMove = new Promise((resolve) => {
    resolveMove = resolve;
  });
  const completed = { ...scanState("completed"), duplicate_groups: 1 };
  const refreshed = { ...completed, duplicate_groups: 0 };
  mocks.listScans.mockResolvedValue({
    scans: [completed],
    total: 1,
    limit: 50,
    offset: 0
  });
  mocks.getGroupSummaries
    .mockResolvedValueOnce({
      scan_id: 1,
      groups: [groupSummary(1)],
      total: 1,
      limit: 100,
      offset: 0,
      filter: "all",
      sort: "recommended"
    })
    .mockResolvedValueOnce({
      scan_id: 1,
      groups: [],
      total: 0,
      limit: 100,
      offset: 0,
      filter: "all",
      sort: "recommended"
    });
  mocks.getGroup.mockResolvedValue(duplicateMoveGroup());
  mocks.getScan.mockResolvedValue(refreshed);
  mocks.moveDuplicateFiles.mockReturnValue(duplicateMove);
  render(<App />);

  await openDuplicatesTab();
  await screen.findByText("Group 1");
  const moveToFolder = screen.getByRole("button", { name: "Move to folder" });
  await waitFor(() => expect(moveToFolder).not.toBeDisabled());
  fireEvent.click(moveToFolder);
  fireEvent.click(await screen.findByRole("button", { name: "Move" }));

  await waitFor(() => expect(mocks.moveDuplicateFiles).toHaveBeenCalledWith(1, 1, [11]));
  expect(screen.getByText("Moving files to duplicate folder")).toBeInTheDocument();
  expect(
    screen.getByText("Moving 1 file(s) into a duplicate folder. This can take a while.")
  ).toBeInTheDocument();
  expect(screen.getByText("Moving selected file(s) to duplicate folder...")).toBeInTheDocument();
  await act(async () => {
    resolveMove({
      moved: [11],
      skipped: [],
      failures: [],
      destination_folder: "/tmp/images/AntiDupeImage_Duplicates_20260615_120000"
    });
    await duplicateMove;
  });
  await waitFor(() =>
    expect(screen.getAllByText(/Moved 1 file to duplicate folder/)).toHaveLength(2)
  );
  expect(
    screen.getAllByText(/Destination: \/tmp\/images\/AntiDupeImage_Duplicates_20260615_120000/)
  ).toHaveLength(2);
  expect(screen.queryByText("Group 1")).not.toBeInTheDocument();
  expect(screen.getByText("No duplicate groups")).toBeInTheDocument();
  expect(screen.getByText("Select a group")).toBeInTheDocument();
  expect(mocks.getScan).toHaveBeenCalledWith(1);
  createObjectURL.mockRestore();
  revokeObjectURL.mockRestore();
});

test("failed duplicate-folder move refreshes group detail and reports failed progress", async () => {
  const createObjectURL = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:thumb");
  const revokeObjectURL = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
  let resolveMove: (value: unknown) => void = () => undefined;
  const duplicateMove = new Promise((resolve) => {
    resolveMove = resolve;
  });
  const completed = { ...scanState("completed"), duplicate_groups: 1 };
  mocks.listScans.mockResolvedValue({
    scans: [completed],
    total: 1,
    limit: 50,
    offset: 0
  });
  mocks.getGroupSummaries.mockResolvedValue({
    scan_id: 1,
    groups: [groupSummary(1)],
    total: 1,
    limit: 100,
    offset: 0,
    filter: "all",
    sort: "recommended"
  });
  const changedAfterMove = duplicateMoveGroup();
  changedAfterMove.images = changedAfterMove.images.map((candidate) =>
    candidate.id === 11
      ? {
          ...candidate,
          available: false,
          status: "changed"
        }
      : candidate
  );
  mocks.getGroup
    .mockResolvedValueOnce(duplicateMoveGroup())
    .mockResolvedValueOnce(changedAfterMove);
  mocks.moveDuplicateFiles.mockReturnValue(duplicateMove);
  render(<App />);

  await openDuplicatesTab();
  await screen.findByText("Group 1");
  const moveToFolder = screen.getByRole("button", { name: "Move to folder" });
  await waitFor(() => expect(moveToFolder).not.toBeDisabled());
  fireEvent.click(moveToFolder);
  fireEvent.click(await screen.findByRole("button", { name: "Move" }));

  await waitFor(() => expect(mocks.moveDuplicateFiles).toHaveBeenCalledWith(1, 1, [11]));
  await act(async () => {
    resolveMove({
      moved: [],
      skipped: [11],
      failures: [
        {
          image_id: 11,
          reason: "File still exists at the original path after duplicate-folder move"
        }
      ]
    });
    await duplicateMove;
  });

  await waitFor(() => expect(screen.getByText("failed")).toBeInTheDocument());
  expect(
    screen.queryByRole("dialog", { name: "Move 1 file(s) to duplicate folder?" })
  ).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Move to folder" })).toBeDisabled();
  expect(
    screen
      .getAllByRole("status")
      .some(
        (status) =>
          status.classList.contains("global-notice") &&
          status.textContent?.includes("No files were moved to the duplicate folder")
      )
  ).toBe(true);
  expect(await screen.findAllByText("Changed after scan")).toHaveLength(2);
  expect(mocks.getGroup).toHaveBeenCalledTimes(2);
  expect(mocks.getGroupSummaries).toHaveBeenCalledTimes(1);
  createObjectURL.mockRestore();
  revokeObjectURL.mockRestore();
});

test("progress stream retries after a temporary REST failure", async () => {
  mocks.listScans.mockResolvedValue({
    scans: [scanState("running")],
    total: 1,
    limit: 50,
    offset: 0
  });
  mocks.getScan.mockRejectedValueOnce(new Error("temporary failure"));
  render(<App />);

  await waitFor(() => expect(mocks.sources).toHaveLength(1));
  const timeout = vi.spyOn(window, "setTimeout").mockImplementation(((handler: TimerHandler) => {
    queueMicrotask(() => {
      if (typeof handler === "function") handler();
    });
    return 1;
  }) as typeof window.setTimeout);
  await act(async () => {
    mocks.sources[0].onerror?.();
    await Promise.resolve();
    await Promise.resolve();
  });
  expect(screen.getByText("Progress stream disconnected: temporary failure")).toBeInTheDocument();

  expect(mocks.sources).toHaveLength(2);
  expect(timeout).toHaveBeenCalled();
  timeout.mockRestore();
});

test("API token controls stay disabled for the full scan submission chain", async () => {
  let resolveCreateScan: (value: { id: number; status: string }) => void = () => undefined;
  const pendingCreateScan = new Promise<{ id: number; status: string }>((resolve) => {
    resolveCreateScan = resolve;
  });
  mocks.createScan.mockReturnValue(pendingCreateScan);
  mocks.getScan.mockResolvedValue(scanState("queued", 12));
  render(<App />);

  fireEvent.change(screen.getByLabelText("Local API token"), { target: { value: "next-token" } });
  fireEvent.click(screen.getByRole("button", { name: "Choose folder" }));
  await waitFor(() =>
    expect(screen.getByLabelText("Image or folder path")).toHaveValue("/tmp/images")
  );
  fireEvent.click(screen.getByRole("button", { name: "Start scan" }));
  await waitFor(() => expect(mocks.createScan).toHaveBeenCalled());

  expect(screen.getByLabelText("Local API token")).toBeDisabled();
  expect(screen.getByRole("button", { name: "Apply" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Apply" }));
  expect(mocks.setApiToken).not.toHaveBeenCalled();

  await act(async () => {
    resolveCreateScan({ id: 12, status: "queued" });
    await pendingCreateScan;
  });
  await waitFor(() => expect(screen.getByRole("button", { name: "Apply" })).not.toBeDisabled());
});

test("changing the API token clears selected scan details", async () => {
  mocks.listScans
    .mockResolvedValueOnce({
      scans: [scanState("completed")],
      total: 1,
      limit: 50,
      offset: 0
    })
    .mockResolvedValue({
      scans: [],
      total: 0,
      limit: 50,
      offset: 0
    });
  mocks.getGroupSummaries.mockResolvedValue({
    scan_id: 1,
    groups: [groupSummary(1)],
    total: 1,
    limit: 100,
    offset: 0,
    filter: "all",
    sort: "recommended"
  });
  render(<App />);

  await openDuplicatesTab();
  expect(await screen.findByText("Group 1")).toBeInTheDocument();
  await openScanTab();
  fireEvent.change(screen.getByLabelText("Local API token"), { target: { value: "new-token" } });
  fireEvent.click(screen.getByRole("button", { name: "Apply" }));

  await waitFor(() => expect(mocks.setApiToken).toHaveBeenCalledWith("new-token"));
  await openDuplicatesTab();
  expect(screen.queryByText("Group 1")).not.toBeInTheDocument();
  expect(screen.getAllByText("Local API token updated.")).toHaveLength(2);
});

test("API token update reports failed verification when backend refresh fails", async () => {
  mocks.getSystem.mockResolvedValueOnce(system).mockRejectedValueOnce(new Error("Unauthorized"));
  mocks.listScans
    .mockResolvedValueOnce({
      scans: [scanState("completed")],
      total: 1,
      limit: 50,
      offset: 0
    })
    .mockRejectedValueOnce(new Error("Unauthorized"));
  mocks.getGroupSummaries.mockResolvedValue({
    scan_id: 1,
    groups: [groupSummary(1)],
    total: 1,
    limit: 100,
    offset: 0,
    filter: "all",
    sort: "recommended"
  });
  render(<App />);

  await openDuplicatesTab();
  expect(await screen.findByText("Group 1")).toBeInTheDocument();
  await openScanTab();
  fireEvent.change(screen.getByLabelText("Local API token"), { target: { value: "wrong-token" } });
  fireEvent.click(screen.getByRole("button", { name: "Apply" }));

  await waitFor(() => expect(mocks.setApiToken).toHaveBeenCalledWith("wrong-token"));
  expect(await screen.findByText("failed")).toBeInTheDocument();
  expect(
    screen.getByText("Token was saved locally, but backend refresh failed. Check the token and retry.")
  ).toBeInTheDocument();
  expect(screen.getByText("Unauthorized")).toBeInTheDocument();
  expect(screen.queryByText("Local API token updated.")).not.toBeInTheDocument();
});
