export type PrecisionMode = "exact_pixel" | "ultra_strict" | "conservative" | "balanced" | "broad";
export type PerformanceProfile = "standard" | "lightweight";
export type GroupFilter = "all" | "visual" | "exact";
export type GroupSort = "recommended" | "score" | "files";

export type Thresholds = {
  phash_distance: number;
  histogram_similarity: number;
  clip_similarity: number;
  clip_enabled: boolean;
  precision_mode: PrecisionMode;
  performance_profile: PerformanceProfile;
};

export type ScanRequest = {
  path: string;
  recursive: boolean;
  model?: string;
  pretrained?: string;
  device: string;
  gpu_index?: number | null;
  allow_clip_fallback: boolean;
  thresholds: Thresholds;
};

export type ScanResponse = {
  id: number;
  status: ScanStatus;
};

export type FolderDialogRequest = {
  initial_path?: string | null;
};

export type FolderDialogResponse = {
  path: string | null;
  cancelled: boolean;
};

export type ScanStatus = "queued" | "running" | "completed" | "failed" | "cancelled";
export type ScanPhase =
  | "queued"
  | "discovering"
  | "extracting"
  | "matching"
  | "verifying"
  | "clip"
  | "grouping"
  | "completed"
  | "failed"
  | "cancelled";

export type ScanState = {
  id: number;
  status: ScanStatus;
  phase: ScanPhase;
  progress_detail: string;
  path: string;
  recursive: boolean;
  model: string;
  pretrained: string;
  device: string;
  thresholds: Thresholds;
  progress: number;
  total_files: number;
  processed_files: number;
  succeeded_files: number;
  failed_files: number;
  skipped_files: number;
  candidate_pairs: number;
  duplicate_groups: number;
  error: string | null;
  warnings: string[];
  created_at: string;
  updated_at: string;
  can_cancel: boolean;
};

export type ScanListResponse = {
  scans: ScanState[];
  total: number;
  limit: number;
  offset: number;
};

export type DeviceInfo = {
  selected: string;
  available: string[];
  cuda_devices: string[];
  mps_available: boolean;
  torch_available: boolean;
  torch_version: string | null;
};

export type ClipInfo = {
  available: boolean;
  version: string | null;
  error: string | null;
};

export type SystemResponse = {
  python: string;
  platform: string;
  app_dir: string;
  db_path: string;
  device: DeviceInfo;
  clip: ClipInfo;
  default_model: string;
  default_pretrained: string;
  token_required: boolean;
};

export type ImageStatus = "available" | "moved" | "trashed" | "missing" | "changed" | "unsafe";

export type MatchEvidence = {
  verified?: boolean;
  match_type?: string;
  precision_mode?: PrecisionMode;
  performance_profile?: PerformanceProfile;
  reduced_evidence?: boolean;
  candidate_limited?: boolean;
  phash_distance?: number;
  dhash_distance?: number;
  whash_distance?: number;
  histogram_similarity?: number;
  layout_similarity?: number;
  structural_similarity?: number;
  edge_similarity?: number;
  quality_score?: number;
  aspect_delta?: number;
  support_signals?: number;
  low_information?: boolean;
  small_difference?: boolean;
  pixel_count_delta?: number;
  clip_similarity?: number;
};

export type ImageSummary = {
  id: number;
  path: string;
  width: number;
  height: number;
  pixel_count: number;
  size_bytes: number;
  phash: string;
  avg_rgb: number[];
  score: number;
  reason: string;
  is_recommended_keep: boolean;
  moved: boolean;
  trashed: boolean;
  status: ImageStatus;
  available: boolean;
  evidence: MatchEvidence | null;
};

export type DuplicateGroup = {
  id: number;
  score: number;
  reason: string;
  recommended_keep_id: number;
  images: ImageSummary[];
};

export type GroupSummary = {
  id: number;
  score: number;
  reason: string;
  recommended_keep_id: number;
  recommended_keep_path: string;
  image_count: number;
  moved_count: number;
};

export type GroupsResponse = {
  scan_id: number;
  groups: DuplicateGroup[];
  total: number;
  limit: number;
  offset: number;
};

export type GroupSummariesResponse = {
  scan_id: number;
  groups: GroupSummary[];
  total: number;
  limit: number;
  offset: number;
  filter: GroupFilter;
  sort: GroupSort;
};

export type ScanFailure = {
  id: number;
  path: string;
  stage: string;
  error: string;
  created_at: string;
};

export type ScanFailuresResponse = {
  scan_id: number;
  failures: ScanFailure[];
  total: number;
  limit: number;
  offset: number;
};

export type MoveDuplicatesRequest = {
  scan_id: number;
  group_id: number;
  image_ids: number[];
  confirm: boolean;
};

export type MoveFailure = {
  image_id: number;
  reason: string;
};

export type MoveDuplicatesResponse = {
  moved: number[];
  skipped: number[];
  failures: MoveFailure[];
  destination_folder: string | null;
};

export type ResetAppDataRequest = {
  confirm: boolean;
};

export type ResetAppDataResponse = {
  scans_deleted: number;
  scan_images_deleted: number;
  groups_deleted: number;
  group_members_deleted: number;
  failures_deleted: number;
  cached_images_deleted: number;
};

export type DeleteResponse = {
  deleted: boolean;
};

export type CancelResponse = {
  id: number;
  status: ScanStatus;
};
