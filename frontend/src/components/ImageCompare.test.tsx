import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { vi } from "vitest";
import { clearThumbnailCache, getThumbnailCacheSize, ImageCompare } from "./ImageCompare";
import type { DuplicateGroup, ImageSummary } from "../types/api";

const mocks = vi.hoisted(() => ({
  fetchThumbnail: vi.fn()
}));

vi.mock("../api/client", () => ({
  fetchThumbnail: mocks.fetchThumbnail
}));

beforeEach(() => {
  clearThumbnailCache();
  vi.clearAllMocks();
  mocks.fetchThumbnail.mockResolvedValue(new Blob(["image"], { type: "image/jpeg" }));
});

function image(id: number, available: boolean): ImageSummary {
  return {
    id,
    path: `/tmp/${id}.png`,
    width: 20,
    height: 20,
    pixel_count: 400,
    size_bytes: 100,
    phash: "0:0:0",
    avg_rgb: [0, 0, 0],
    score: 1,
    reason: id === 1 ? "recommended-keep" : "exact-pixel",
    is_recommended_keep: id === 1,
    moved: false,
    trashed: false,
    status: available ? "available" : "changed",
    available,
    evidence: null
  };
}

function renderCompare(group: DuplicateGroup | null, onMove = vi.fn().mockResolvedValue(true)) {
  return render(
    <ImageCompare
      group={group}
      loading={false}
      error={null}
      onRetry={vi.fn()}
      onMove={onMove}
      busy={false}
    />
  );
}

test("unavailable images are not preselected and confirmation explains the move", async () => {
  const group: DuplicateGroup = {
    id: 1,
    score: 1,
    reason: "exact-pixel",
    recommended_keep_id: 1,
    images: [image(1, true), image(2, false), image(3, true)]
  };
  renderCompare(group);

  fireEvent.click(screen.getByRole("button", { name: "Move to folder" }));

  const dialog = screen.getByRole("dialog");
  expect(within(dialog).getByRole("heading", { name: "Move 1 file(s) to duplicate folder?" })).toBeInTheDocument();
  expect(within(dialog).getByText("/tmp/1.png")).toBeInTheDocument();
  expect(within(dialog).getByText("/tmp/3.png")).toBeInTheDocument();
  expect(within(dialog).getByText("1 file(s) / 100 B")).toBeInTheDocument();
  expect(within(dialog).getByText(/AntiDupeImage_Duplicates_/)).toBeInTheDocument();
  expect(within(dialog).getByText(/does not provide automatic restore/)).toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus());
});

test("move confirmation stays open while pending and closes after a successful move", async () => {
  let resolveMove: (value: boolean) => void = () => undefined;
  const movePromise = new Promise<boolean>((resolve) => {
    resolveMove = resolve;
  });
  const onMove = vi.fn().mockReturnValue(movePromise);
  const group: DuplicateGroup = {
    id: 1,
    score: 1,
    reason: "exact-pixel",
    recommended_keep_id: 1,
    images: [image(1, true), image(2, true)]
  };
  renderCompare(group, onMove);

  fireEvent.click(screen.getByRole("button", { name: "Move to folder" }));
  expect(screen.getByRole("dialog")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Move" }));

  expect(onMove).toHaveBeenCalledWith(1, [2]);
  expect(screen.getByRole("dialog")).toBeInTheDocument();

  await act(async () => {
    resolveMove(true);
    await movePromise;
  });

  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

test("move confirmation traps focus and restores it to the trigger", async () => {
  const group: DuplicateGroup = {
    id: 1,
    score: 1,
    reason: "exact-pixel",
    recommended_keep_id: 1,
    images: [image(1, true), image(2, true)]
  };
  renderCompare(group);
  const trigger = screen.getByRole("button", { name: "Move to folder" });

  fireEvent.click(trigger);
  const dialog = screen.getByRole("dialog");
  const cancel = within(dialog).getByRole("button", { name: "Cancel" });
  const move = within(dialog).getByRole("button", { name: "Move" });
  await waitFor(() => expect(cancel).toHaveFocus());

  fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
  expect(move).toHaveFocus();
  fireEvent.keyDown(document, { key: "Tab" });
  expect(cancel).toHaveFocus();
  fireEvent.keyDown(document, { key: "Escape" });

  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  await waitFor(() => expect(trigger).toHaveFocus());
});

test("move confirmation cannot be dismissed while pending", () => {
  const onMove = vi.fn().mockReturnValue(new Promise<boolean>(() => undefined));
  const group: DuplicateGroup = {
    id: 1,
    score: 1,
    reason: "exact-pixel",
    recommended_keep_id: 1,
    images: [image(1, true), image(2, true)]
  };
  const { rerender } = render(
    <ImageCompare
      group={group}
      loading={false}
      error={null}
      onRetry={vi.fn()}
      onMove={onMove}
      busy={false}
    />
  );

  fireEvent.click(screen.getByRole("button", { name: "Move to folder" }));
  fireEvent.click(screen.getByRole("button", { name: "Move" }));
  rerender(
    <ImageCompare
      group={group}
      loading={false}
      error={null}
      onRetry={vi.fn()}
      onMove={onMove}
      busy={true}
    />
  );
  fireEvent.keyDown(window, { key: "Escape" });

  expect(screen.getByRole("dialog")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
});

test("move confirmation stays open when the move is not completed", async () => {
  const onMove = vi.fn().mockResolvedValue(false);
  const group: DuplicateGroup = {
    id: 1,
    score: 1,
    reason: "exact-pixel",
    recommended_keep_id: 1,
    images: [image(1, true), image(2, true)]
  };
  renderCompare(group, onMove);

  fireEvent.click(screen.getByRole("button", { name: "Move to folder" }));
  fireEvent.click(screen.getByRole("button", { name: "Move" }));

  await waitFor(() => expect(onMove).toHaveBeenCalledWith(1, [2]));
  expect(screen.getByRole("dialog")).toBeInTheDocument();
  expect(await screen.findByRole("alert")).toHaveTextContent("Move was not completed.");
});

test("unavailable member status is shown with user-facing wording", () => {
  const moved = image(2, false);
  moved.status = "moved";
  const group: DuplicateGroup = {
    id: 1,
    score: 1,
    reason: "exact-pixel",
    recommended_keep_id: 1,
    images: [image(1, true), moved]
  };
  renderCompare(group);

  expect(screen.getAllByText("Moved to duplicate folder").length).toBeGreaterThan(0);
});

test("visual groups never preselect mixed exact or visual candidates", () => {
  const exact = image(2, true);
  const visual = image(2, true);
  visual.id = 3;
  visual.path = "/tmp/3.png";
  visual.reason = "visual-hash";
  visual.evidence = {
    verified: true,
    precision_mode: "conservative",
    performance_profile: "standard",
    phash_distance: 4,
    dhash_distance: 6,
    whash_distance: 8,
    layout_similarity: 0.93,
    structural_similarity: 0.97,
    edge_similarity: 0.88,
    histogram_similarity: 0.96,
    quality_score: 0.44
  };
  const group: DuplicateGroup = {
    id: 1,
    score: 0.95,
    reason: "visual-hash",
    recommended_keep_id: 1,
    images: [image(1, true), exact, visual]
  };
  renderCompare(group);

  expect(screen.getByRole("button", { name: "Move to folder" })).toBeDisabled();
  expect(screen.getByRole("checkbox", { name: "Move /tmp/2.png" })).not.toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Move /tmp/3.png" })).not.toBeChecked();
  expect(screen.getByText(/Visual candidates are never preselected/)).toBeInTheDocument();
  expect(screen.getByText("Conservative")).toBeInTheDocument();
  expect(screen.getByText("pHash 4")).toBeInTheDocument();
  expect(screen.getByText("Layout 93%")).toBeInTheDocument();
  expect(screen.getByText("Structure 97%")).toBeInTheDocument();
});

test("candidate controls and confirmation distinguish duplicate basenames by path", () => {
  const first = image(2, true);
  first.path = "/tmp/album-a/same.png";
  const second = image(3, true);
  second.path = "/tmp/album-b/same.png";
  const group: DuplicateGroup = {
    id: 1,
    score: 1,
    reason: "exact-pixel",
    recommended_keep_id: 1,
    images: [image(1, true), first, second]
  };
  renderCompare(group);

  expect(screen.getByRole("button", { name: "Keep /tmp/album-a/same.png" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Keep /tmp/album-b/same.png" })).toBeInTheDocument();
  expect(screen.getByRole("checkbox", { name: "Move /tmp/album-a/same.png" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Move /tmp/album-b/same.png" })).toBeChecked();

  fireEvent.click(screen.getByRole("button", { name: "Move to folder" }));
  const dialog = screen.getByRole("dialog");
  expect(within(dialog).getByText("/tmp/album-a/same.png")).toBeInTheDocument();
  expect(within(dialog).getByText("/tmp/album-b/same.png")).toBeInTheDocument();
});

test("evidence summary aggregates all visual candidate metrics", () => {
  const first = image(2, true);
  first.reason = "visual-hash";
  first.evidence = {
    verified: true,
    precision_mode: "conservative",
    performance_profile: "standard",
    phash_distance: 4,
    dhash_distance: 6,
    whash_distance: 8,
    layout_similarity: 0.93,
    structural_similarity: 0.94,
    edge_similarity: 0.88,
    histogram_similarity: 0.96,
    quality_score: 0.44
  };
  const second = image(3, true);
  second.reason = "small-difference";
  second.evidence = {
    verified: true,
    match_type: "small-difference",
    precision_mode: "conservative",
    performance_profile: "standard",
    small_difference: true,
    phash_distance: 9,
    dhash_distance: 2,
    whash_distance: 3,
    layout_similarity: 0.97,
    structural_similarity: 0.98,
    edge_similarity: 0.95,
    histogram_similarity: 0.99,
    quality_score: 0.62
  };
  const group: DuplicateGroup = {
    id: 1,
    score: 0.92,
    reason: "small-difference",
    recommended_keep_id: 1,
    images: [image(1, true), first, second]
  };

  renderCompare(group);

  const evidenceToggle = screen.getByText("一致の根拠");
  expect(evidenceToggle.closest("details")).not.toHaveAttribute("open");
  fireEvent.click(evidenceToggle);
  expect(evidenceToggle.closest("details")).toHaveAttribute("open");
  expect(screen.getByRole("row", { name: /Candidate evidence 2 candidates Aggregated/ })).toBeInTheDocument();
  expect(screen.getByRole("row", { name: /pHash 4-9 Passed across 2 candidates/ })).toBeInTheDocument();
  expect(screen.getByRole("row", { name: /Luma layout 93%-97% Passed across 2 candidates/ })).toBeInTheDocument();
  expect(screen.getByRole("row", { name: /Structure 94%-98% Passed across 2 candidates/ })).toBeInTheDocument();
  expect(screen.getByRole("row", { name: /Small difference 1\/2 candidates Extra support required/ })).toBeInTheDocument();
});

test("small difference evidence is labeled", () => {
  const edited = image(2, true);
  edited.reason = "small-difference";
  edited.evidence = {
    verified: true,
    match_type: "small-difference",
    precision_mode: "conservative",
    performance_profile: "standard",
    small_difference: true,
    phash_distance: 11,
    dhash_distance: 1,
    whash_distance: 2,
    layout_similarity: 0.87,
    edge_similarity: 0.95,
    histogram_similarity: 0.99,
    quality_score: 0.63
  };
  const group: DuplicateGroup = {
    id: 1,
    score: 0.95,
    reason: "small-difference",
    recommended_keep_id: 1,
    images: [image(1, true), edited]
  };

  renderCompare(group);

  expect(screen.getByRole("button", { name: "Move to folder" })).toBeDisabled();
  expect(screen.getByText("Small diff")).toBeInTheDocument();
  expect(screen.getByText("pHash 11")).toBeInTheDocument();
});

test("lightweight evidence is labeled", () => {
  const visual = image(2, true);
  visual.reason = "visual-hash";
  visual.evidence = {
    verified: true,
    precision_mode: "balanced",
    performance_profile: "lightweight",
    reduced_evidence: true,
    candidate_limited: true,
    phash_distance: 7,
    layout_similarity: 0.8,
    histogram_similarity: 0.85
  };
  const group: DuplicateGroup = {
    id: 1,
    score: 0.8,
    reason: "visual-hash",
    recommended_keep_id: 1,
    images: [image(1, true), visual]
  };

  renderCompare(group);

  expect(screen.getByText("Lightweight")).toBeInTheDocument();
  expect(screen.getByText("Reduced")).toBeInTheDocument();
  expect(screen.getByText("Limited")).toBeInTheDocument();
});

test("large groups render candidates in bounded batches", () => {
  const group: DuplicateGroup = {
    id: 1,
    score: 1,
    reason: "exact-pixel",
    recommended_keep_id: 1,
    images: Array.from({ length: 55 }, (_, index) => image(index + 1, true))
  };

  const { container } = renderCompare(group);

  expect(container.querySelectorAll(".member-row")).toHaveLength(50);
  fireEvent.click(screen.getByRole("button", { name: "Show 4 more" }));
  expect(container.querySelectorAll(".member-row")).toHaveLength(54);
});

test("thumbnail blobs are loaded and revoked on unmount", async () => {
  const createObjectURL = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:thumb");
  const revokeObjectURL = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
  const group: DuplicateGroup = {
    id: 1,
    score: 1,
    reason: "exact-pixel",
    recommended_keep_id: 1,
    images: [image(1, true), image(2, true)]
  };

  const { unmount } = renderCompare(group);

  expect(await screen.findByAltText("1.png")).toHaveAttribute("src", "blob:thumb");
  await waitFor(() => expect(mocks.fetchThumbnail).toHaveBeenCalledWith(2, expect.any(AbortSignal)));

  unmount();

  expect(createObjectURL).toHaveBeenCalled();
  expect(revokeObjectURL).toHaveBeenCalledWith("blob:thumb");
  createObjectURL.mockRestore();
  revokeObjectURL.mockRestore();
});

test("started thumbnail requests are aborted on unmount", async () => {
  const startedSignals: AbortSignal[] = [];
  mocks.fetchThumbnail.mockImplementation((_imageId: number, signal: AbortSignal) => {
    startedSignals.push(signal);
    return new Promise<Blob>((_resolve, reject) => {
      signal.addEventListener("abort", () => reject(new Error("aborted")));
    });
  });
  const group: DuplicateGroup = {
    id: 1,
    score: 1,
    reason: "exact-pixel",
    recommended_keep_id: 1,
    images: [1, 2, 3, 4, 5, 6].map((id) => image(id, true))
  };

  const { unmount } = renderCompare(group);

  await waitFor(() => expect(mocks.fetchThumbnail).toHaveBeenCalledTimes(4));
  unmount();

  expect(startedSignals).toHaveLength(4);
  expect(startedSignals.every((signal) => signal.aborted)).toBe(true);
});

test("clearing thumbnail cache aborts active requests and allows later requests", async () => {
  const startedSignals: AbortSignal[] = [];
  mocks.fetchThumbnail.mockImplementation((_imageId: number, signal: AbortSignal) => {
    startedSignals.push(signal);
    return new Promise<Blob>((_resolve, reject) => {
      signal.addEventListener("abort", () => reject(new Error("aborted")));
    });
  });
  const group: DuplicateGroup = {
    id: 1,
    score: 1,
    reason: "exact-pixel",
    recommended_keep_id: 1,
    images: [1, 2, 3, 4, 5, 6].map((id) => image(id, true))
  };
  const { unmount } = renderCompare(group);

  await waitFor(() => expect(mocks.fetchThumbnail).toHaveBeenCalledTimes(4));
  clearThumbnailCache();

  expect(startedSignals).toHaveLength(4);
  expect(startedSignals.every((signal) => signal.aborted)).toBe(true);
  unmount();

  mocks.fetchThumbnail.mockResolvedValue(new Blob(["image"], { type: "image/jpeg" }));
  renderCompare({
    id: 2,
    score: 1,
    reason: "exact-pixel",
    recommended_keep_id: 7,
    images: [image(7, true)]
  });

  await waitFor(() => expect(mocks.fetchThumbnail).toHaveBeenCalledTimes(5));
});

test("clearing thumbnail cache ignores stale responses from cancelled requests", async () => {
  let resolveStale: (blob: Blob) => void = () => undefined;
  mocks.fetchThumbnail.mockImplementationOnce(
    () =>
      new Promise<Blob>((resolve) => {
        resolveStale = resolve;
      })
  );
  const group: DuplicateGroup = {
    id: 1,
    score: 1,
    reason: "exact-pixel",
    recommended_keep_id: 1,
    images: [image(1, true)]
  };
  const { unmount } = renderCompare(group);

  await waitFor(() => expect(mocks.fetchThumbnail).toHaveBeenCalledTimes(1));
  clearThumbnailCache();
  await act(async () => {
    resolveStale(new Blob(["stale"], { type: "image/jpeg" }));
    await Promise.resolve();
  });
  unmount();

  mocks.fetchThumbnail.mockResolvedValue(new Blob(["fresh"], { type: "image/jpeg" }));
  renderCompare(group);

  await waitFor(() => expect(mocks.fetchThumbnail).toHaveBeenCalledTimes(2));
});

test("thumbnail requests are concurrency limited", async () => {
  const createObjectURL = vi.spyOn(URL, "createObjectURL").mockImplementation(
    () => `blob:thumb-${createObjectURL.mock.calls.length}`
  );
  const revokeObjectURL = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
  const deferred = Array.from({ length: 6 }, () => {
    let resolve: (blob: Blob) => void = () => undefined;
    const promise = new Promise<Blob>((next) => {
      resolve = next;
    });
    return { promise, resolve };
  });
  let index = 0;
  mocks.fetchThumbnail.mockImplementation(() => deferred[index++].promise);
  const group: DuplicateGroup = {
    id: 1,
    score: 1,
    reason: "exact-pixel",
    recommended_keep_id: 1,
    images: [1, 2, 3, 4, 5, 6].map((id) => image(id, true))
  };

  const { unmount } = renderCompare(group);

  await waitFor(() => expect(mocks.fetchThumbnail).toHaveBeenCalledTimes(4));
  deferred[0].resolve(new Blob(["image"], { type: "image/jpeg" }));
  await waitFor(() => expect(mocks.fetchThumbnail).toHaveBeenCalledTimes(5));
  deferred.slice(1).forEach((item) => item.resolve(new Blob(["image"], { type: "image/jpeg" })));
  await screen.findByAltText("6.png");

  unmount();
  createObjectURL.mockRestore();
  revokeObjectURL.mockRestore();
});

test("duplicate thumbnail requests share one fetch", async () => {
  let resolveThumbnail: (blob: Blob) => void = () => undefined;
  const thumbnail = new Promise<Blob>((resolve) => {
    resolveThumbnail = resolve;
  });
  mocks.fetchThumbnail.mockReturnValue(thumbnail);
  const group: DuplicateGroup = {
    id: 1,
    score: 1,
    reason: "exact-pixel",
    recommended_keep_id: 1,
    images: [image(1, true), image(2, true)]
  };

  render(
    <>
      <ImageCompare
        group={group}
        loading={false}
        error={null}
        onRetry={vi.fn()}
        onMove={vi.fn().mockResolvedValue(true)}
        busy={false}
      />
      <ImageCompare
        group={group}
        loading={false}
        error={null}
        onRetry={vi.fn()}
        onMove={vi.fn().mockResolvedValue(true)}
        busy={false}
      />
    </>
  );

  await waitFor(() => expect(mocks.fetchThumbnail).toHaveBeenCalledTimes(2));
  await act(async () => {
    resolveThumbnail(new Blob(["image"], { type: "image/jpeg" }));
    await thumbnail;
  });

  expect(mocks.fetchThumbnail).toHaveBeenCalledTimes(2);
});

test("thumbnail blob cache evicts least recently used entries", async () => {
  const createObjectURL = vi.spyOn(URL, "createObjectURL").mockImplementation(
    () => `blob:thumb-${createObjectURL.mock.calls.length}`
  );
  const revokeObjectURL = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
  const group: DuplicateGroup = {
    id: 1,
    score: 1,
    reason: "exact-pixel",
    recommended_keep_id: 1,
    images: Array.from({ length: 40 }, (_, index) => image(index + 1, true))
  };
  const { unmount } = renderCompare(group);

  await waitFor(() => expect(mocks.fetchThumbnail).toHaveBeenCalledTimes(40));
  expect(getThumbnailCacheSize()).toBe(32);
  unmount();

  renderCompare({
    id: 2,
    score: 1,
    reason: "exact-pixel",
    recommended_keep_id: 1,
    images: [image(1, true)]
  });

  await waitFor(() => expect(mocks.fetchThumbnail).toHaveBeenCalledTimes(41));
  createObjectURL.mockRestore();
  revokeObjectURL.mockRestore();
});
