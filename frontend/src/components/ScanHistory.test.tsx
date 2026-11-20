import { fireEvent, render, screen } from "@testing-library/react";
import { vi } from "vitest";
import type { ScanState } from "../types/api";
import { formatLocalTimestamp } from "../utils/dateTime";
import { ScanHistory } from "./ScanHistory";

const completedScan: ScanState = {
  id: 21,
  status: "completed",
  phase: "completed",
  progress_detail: "Completed with duplicate groups",
  path: "C:\\photos",
  recursive: true,
  model: "ViT-B-32",
  pretrained: "openai",
  device: "cuda:0",
  thresholds: {
    phash_distance: 10,
    histogram_similarity: 0.9,
    clip_similarity: 0.92,
    clip_enabled: true,
    precision_mode: "ultra_strict",
    performance_profile: "lightweight"
  },
  progress: 1,
  total_files: 25,
  processed_files: 24,
  succeeded_files: 20,
  failed_files: 2,
  skipped_files: 3,
  candidate_pairs: 9,
  duplicate_groups: 4,
  error: null,
  warnings: [],
  created_at: "2026-06-12T00:00:00Z",
  updated_at: "2026-06-12T00:01:15Z",
  can_cancel: false
};

test("shows comparison metrics and reports status filter changes", () => {
  const onFilter = vi.fn();
  render(
    <ScanHistory
      scans={[completedScan]}
      total={1}
      limit={20}
      offset={0}
      selectedId={completedScan.id}
      filter="all"
      busy={false}
      onSelect={vi.fn()}
      onReuse={vi.fn()}
      onCancel={vi.fn()}
      onDelete={vi.fn()}
      onRefresh={vi.fn()}
      onPage={vi.fn()}
      onFilter={onFilter}
    />
  );

  expect(screen.getByText(formatLocalTimestamp(completedScan.created_at))).toBeInTheDocument();
  expect(screen.getByText("1m 15s")).toBeInTheDocument();
  expect(screen.getByText("25 files")).toBeInTheDocument();
  expect(screen.getByText("20 succeeded")).toBeInTheDocument();
  expect(screen.getByText("2 failed")).toBeInTheDocument();
  expect(screen.getByText("3 skipped")).toBeInTheDocument();
  expect(screen.getByText("4 groups")).toBeInTheDocument();
  expect(screen.getByText("Ultra Strict")).toBeInTheDocument();
  expect(screen.getByText(/completed \/ Lightweight/i)).toBeInTheDocument();

  for (const status of ["completed", "failed", "cancelled"] as const) {
    fireEvent.change(screen.getByLabelText("History status"), { target: { value: status } });
  }
  expect(onFilter.mock.calls).toEqual([["completed"], ["failed"], ["cancelled"]]);
});
