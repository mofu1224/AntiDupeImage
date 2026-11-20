import { fireEvent, render, screen } from "@testing-library/react";
import { vi } from "vitest";
import { ProgressPanel } from "./ProgressPanel";
import type { ScanState } from "../types/api";

function scanState(): ScanState {
  return {
    id: 12,
    status: "running",
    phase: "matching",
    progress_detail: "Matching 20 image(s)",
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
    progress: 0.6,
    total_files: 20,
    processed_files: 20,
    succeeded_files: 20,
    failed_files: 0,
    skipped_files: 0,
    candidate_pairs: 4,
    duplicate_groups: 0,
    error: null,
    warnings: [],
    created_at: "2026-06-12 00:00:00",
    updated_at: "2026-06-12 00:00:00",
    can_cancel: true
  };
}

test("shows scan phase and progress detail", () => {
  render(<ProgressPanel scan={scanState()} />);

  expect(screen.getByText("Scan #12 / matching")).toBeInTheDocument();
  expect(screen.getByText("Processed")).toBeInTheDocument();
  expect(screen.getByText("20/20")).toBeInTheDocument();
  expect(screen.getByText("Matching 20 image(s)")).toBeInTheDocument();
});

test("offers duplicate review only for completed scans with groups", () => {
  const onViewDuplicates = vi.fn();
  const scan = scanState();
  scan.status = "completed";
  scan.phase = "completed";
  scan.progress = 1;
  scan.duplicate_groups = 3;

  render(<ProgressPanel scan={scan} onViewDuplicates={onViewDuplicates} />);

  fireEvent.click(screen.getByRole("button", { name: "Review duplicates" }));
  expect(onViewDuplicates).toHaveBeenCalledTimes(1);
  expect(screen.queryByText(/No duplicate groups were found/)).not.toBeInTheDocument();
});

test("shows a clear empty result and does not repeat a failed scan error", () => {
  const completed = scanState();
  completed.status = "completed";
  completed.phase = "completed";
  completed.progress = 1;
  completed.duplicate_groups = 0;
  const { rerender } = render(<ProgressPanel scan={completed} onViewDuplicates={vi.fn()} />);

  expect(screen.getByText("No duplicate groups were found in this scan.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Review duplicates" })).not.toBeInTheDocument();

  const failed = scanState();
  failed.status = "failed";
  failed.phase = "failed";
  failed.error = "Image decoder stopped unexpectedly";
  failed.progress_detail = failed.error;
  rerender(<ProgressPanel scan={failed} />);

  expect(screen.getAllByText("Image decoder stopped unexpectedly")).toHaveLength(1);
});
