import { AlertTriangle, Check, FileCheck2, FolderInput, ImageOff, Info, ShieldCheck } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { fetchThumbnail } from "../api/client";
import type { DuplicateGroup, ImageSummary, MatchEvidence } from "../types/api";

const MAX_THUMBNAIL_REQUESTS = 4;
const INITIAL_VISIBLE_CANDIDATES = 50;
const VISIBLE_CANDIDATE_INCREMENT = 50;
const MAX_THUMBNAIL_CACHE_ITEMS = 32;
const MAX_CONFIRMATION_FILENAMES = 50;

type Props = {
  group: DuplicateGroup | null;
  loading: boolean;
  error: string | null;
  onMove: (groupId: number, imageIds: number[]) => Promise<boolean>;
  onRetry: () => void;
  busy: boolean;
};

type ThumbnailTask = {
  imageId: number;
  promise: Promise<Blob>;
  resolve: (blob: Blob) => void;
  reject: (error: unknown) => void;
  controller: AbortController;
  cancelled: boolean;
  started: boolean;
  consumers: number;
};

const thumbnailQueue: ThumbnailTask[] = [];
const queuedThumbnailRequests = new Map<number, ThumbnailTask>();
const activeThumbnailRequestsById = new Map<number, ThumbnailTask>();
const thumbnailBlobCache = new Map<number, Blob>();
let activeThumbnailRequests = 0;

export function ImageCompare({ group, loading, error, onMove, onRetry, busy }: Props) {
  const [keepId, setKeepId] = useState<number | null>(null);
  const [selectedMove, setSelectedMove] = useState<Set<number>>(new Set());
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [moveIncomplete, setMoveIncomplete] = useState(false);
  const [visibleCandidateCount, setVisibleCandidateCount] = useState(INITIAL_VISIBLE_CANDIDATES);
  const cancelMoveRef = useRef<HTMLButtonElement>(null);
  const moveTriggerRef = useRef<HTMLButtonElement>(null);
  const moveDialogRef = useRef<HTMLDivElement>(null);
  const confirmationWasOpenRef = useRef(false);

  useEffect(() => {
    if (!group) {
      setKeepId(null);
      setSelectedMove(new Set());
      setConfirmOpen(false);
      setMoveIncomplete(false);
      setVisibleCandidateCount(INITIAL_VISIBLE_CANDIDATES);
      return;
    }
    const availableImages = group.images.filter((image) => image.available);
    const recommended = availableImages.find((image) => image.id === group.recommended_keep_id);
    const nextKeepId = recommended?.id ?? availableImages[0]?.id ?? null;
    setKeepId(nextKeepId);
    setSelectedMove(defaultMoveSelection(group, nextKeepId));
    setConfirmOpen(false);
    setMoveIncomplete(false);
    setVisibleCandidateCount(INITIAL_VISIBLE_CANDIDATES);
  }, [group]);

  useEffect(() => {
    let animationFrame = 0;
    if (confirmOpen) {
      confirmationWasOpenRef.current = true;
      animationFrame = window.requestAnimationFrame(() => cancelMoveRef.current?.focus());
    } else if (confirmationWasOpenRef.current) {
      confirmationWasOpenRef.current = false;
      animationFrame = window.requestAnimationFrame(() => moveTriggerRef.current?.focus());
    }
    return () => window.cancelAnimationFrame(animationFrame);
  }, [confirmOpen]);

  useEffect(() => {
    if (!confirmOpen) return;
    function manageConfirmationKeyboard(event: KeyboardEvent) {
      if (event.key === "Escape") {
        if (!busy) setConfirmOpen(false);
        return;
      }
      if (event.key !== "Tab") return;
      const dialog = moveDialogRef.current;
      if (!dialog) return;
      const focusable = Array.from(
        dialog.querySelectorAll<HTMLElement>(
          'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])'
        )
      );
      if (focusable.length === 0) {
        event.preventDefault();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      if (!dialog.contains(active)) {
        event.preventDefault();
        first.focus();
      } else if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", manageConfirmationKeyboard);
    return () => document.removeEventListener("keydown", manageConfirmationKeyboard);
  }, [busy, confirmOpen]);

  const selectedImages = useMemo(
    () => group?.images.filter((image) => image.available && selectedMove.has(image.id)) ?? [],
    [group, selectedMove]
  );
  const selectedIds = useMemo(() => selectedImages.map((image) => image.id), [selectedImages]);
  const availableCount = group?.images.filter((image) => image.available).length ?? 0;
  const keepImage = group?.images.find((image) => image.id === keepId) ?? null;
  const candidateImages = group && keepId !== null ? group.images.filter((image) => image.id !== keepId) : [];
  const visibleCandidateImages = candidateImages.slice(0, visibleCandidateCount);
  const selectedBytes = selectedImages.reduce((total, image) => total + image.size_bytes, 0);
  const canMove = Boolean(group && keepId !== null && selectedIds.length > 0 && selectedIds.length < availableCount);

  if (error) {
    return (
      <section className="panel compare-panel blank">
        <AlertTriangle size={34} />
        <span>{error}</span>
        <button className="secondary-button" onClick={onRetry}>
          Retry
        </button>
      </section>
    );
  }

  if (loading && !group) {
    return (
      <section className="panel compare-panel blank">
        <Info size={34} className="spin" />
        <span>Loading group details</span>
      </section>
    );
  }

  if (!group) {
    return (
      <section className="panel compare-panel blank">
        <ShieldCheck size={34} />
        <span>Select a group</span>
      </section>
    );
  }

  function markKeep(image: ImageSummary) {
    if (!image.available) return;
    setKeepId(image.id);
    setSelectedMove(defaultMoveSelection(group!, image.id));
  }

  function toggleMove(image: ImageSummary) {
    if (!image.available) return;
    setSelectedMove((current) => {
      const next = new Set(current);
      if (next.has(image.id)) {
        next.delete(image.id);
      } else {
        next.add(image.id);
      }
      return next;
    });
  }

  async function confirmMove() {
    if (!group || !canMove) return;
    setMoveIncomplete(false);
    if (await onMove(group.id, selectedIds)) {
      setConfirmOpen(false);
    } else {
      setMoveIncomplete(true);
    }
  }

  return (
    <section className="panel compare-panel">
      <div className="panel-header">
        <div>
          <h2 title={`Group ${group.id}`}>Compare inspector</h2>
        </div>
        <span className={`match-pill ${group.reason === "exact-pixel" ? "exact" : "visual"}`}>
          {group.reason === "exact-pixel" ? "Exact match" : "Visual match"}
        </span>
      </div>
      {loading ? <div className="inline-loading">Refreshing group details</div> : null}

      <div className="compare-layout">
        <section className="keep-inspector" aria-label="Recommended keep image">
          <div className="inspector-heading">
            <div>
              <span>Recommended keep</span>
              <strong>{keepImage ? basename(keepImage.path) : "No available image"}</strong>
            </div>
            <span className="keep-status">
              <ShieldCheck size={15} />
              Keep
            </span>
          </div>
          {keepImage ? (
            <article className={`keep-card ${!keepImage.available ? "unavailable" : ""}`}>
              <div className="thumb-wrap large">
                {keepImage.available ? (
                  <ThumbnailImage image={keepImage} />
                ) : (
                  <UnavailableThumb image={keepImage} />
                )}
              </div>
              <div className="keep-meta">
                <strong>{keepImage.is_recommended_keep ? "Recommended" : "Selected"}</strong>
                <small title={keepImage.path}>{keepImage.path}</small>
                <dl>
                  <div>
                    <dt>Dimensions</dt>
                    <dd>
                      {keepImage.width} x {keepImage.height}
                    </dd>
                  </div>
                  <div>
                    <dt>File size</dt>
                    <dd>{formatBytes(keepImage.size_bytes)}</dd>
                  </div>
                  <div>
                    <dt>Direct score</dt>
                    <dd>{Math.round(keepImage.score * 100)}%</dd>
                  </div>
                </dl>
              </div>
            </article>
          ) : (
            <div className="empty-state compact">
              <ImageOff size={24} />
              <span>No keep image available</span>
            </div>
          )}
        </section>

        <section className="candidate-panel" aria-label="Group members">
          <div className="inspector-heading">
            <div>
              <strong>Candidates ({candidateImages.length})</strong>
            </div>
            <span>{availableCount} available</span>
          </div>

          <div className="member-list">
            {candidateImages.length === 0 ? (
              <div className="empty-state compact">
                <FileCheck2 size={24} />
                <span>No candidates after the keep image</span>
              </div>
            ) : (
              visibleCandidateImages.map((image) => {
                const isSelectedMove = selectedMove.has(image.id);
                return (
                  <article
                    key={image.id}
                    className={`member-row ${isSelectedMove ? "move-selected" : ""} ${
                      !image.available ? "unavailable" : ""
                    }`}
                  >
                    <div className="member-thumb">
                      {image.available ? <ThumbnailImage image={image} /> : <UnavailableThumb image={image} />}
                    </div>
                    <div className="member-meta">
                      <strong title={image.path}>{basename(image.path)}</strong>
                      <span title={image.path}>{image.path}</span>
                      <span>
                        {image.width} x {image.height} / {formatBytes(image.size_bytes)}
                      </span>
                      <span>
                        {Math.round(image.score * 100)}%
                      </span>
                      {!image.available ? <span>{statusLabel(image.status)}</span> : null}
                      <EvidenceChips image={image} />
                    </div>
                    <div className="member-actions">
                      <button
                        className="secondary-button"
                        onClick={() => markKeep(image)}
                        disabled={!image.available || busy}
                        aria-label={`Keep ${image.path}`}
                      >
                        <Check size={16} />
                        <span>Keep</span>
                      </button>
                      <label className="move-toggle">
                        <input
                          type="checkbox"
                          checked={isSelectedMove}
                          disabled={!image.available || busy}
                          onChange={() => toggleMove(image)}
                          aria-label={`Move ${image.path}`}
                        />
                        <span>Move</span>
                      </label>
                    </div>
                  </article>
                );
              })
            )}
          </div>
          {visibleCandidateImages.length < candidateImages.length ? (
            <button
              className="secondary-button"
              onClick={() =>
                setVisibleCandidateCount((current) =>
                  Math.min(current + VISIBLE_CANDIDATE_INCREMENT, candidateImages.length)
                )
              }
              disabled={busy}
            >
              Show {Math.min(VISIBLE_CANDIDATE_INCREMENT, candidateImages.length - visibleCandidateImages.length)} more
            </button>
          ) : null}
        </section>
      </div>

      <details className="evidence-details">
        <summary>一致の根拠</summary>
        <EvidenceSummary group={group} />
      </details>

      {group.reason !== "exact-pixel" ? (
        <p className="warning-box compare-warning">
          <AlertTriangle size={16} />
          <span>Visual candidates are never preselected.</span>
        </p>
      ) : null}

      <div className="move-ledger">
        <div className="move-ledger-main">
          <FolderInput size={26} />
          <div>
            <strong>{selectedIds.length} selected</strong>
            <span>{formatBytes(selectedBytes)}</span>
          </div>
        </div>
        <button
          ref={moveTriggerRef}
          className="danger-button"
          disabled={!canMove || busy}
          onClick={() => setConfirmOpen(true)}
        >
          <FolderInput size={18} />
          <span>Move to folder</span>
        </button>
      </div>

      {/*
        The modal is intentionally separate from the persistent safety bar so the
        final file move still requires an explicit second step.
      */}
      {confirmOpen ? (
        <div className="modal-backdrop" role="presentation">
          <div
            ref={moveDialogRef}
            className="modal move-confirmation-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="move-title"
            aria-describedby="move-description"
          >
            <h3 id="move-title">Move {selectedIds.length} file(s) to duplicate folder?</h3>
            <p id="move-description">
              This moves files instead of deleting them.
            </p>
            <dl className="move-confirmation-summary">
              <div>
                <dt>Keep</dt>
                <dd>{group.images.find((image) => image.id === keepId)?.path ?? ""}</dd>
              </div>
              <div>
                <dt>Move</dt>
                <dd>{selectedIds.length} file(s) / {formatBytes(selectedBytes)}</dd>
              </div>
              <div>
                <dt>Destination</dt>
                <dd>
                  <code>AntiDupeImage_Duplicates_*</code> / scan root
                </dd>
              </div>
            </dl>
            <div className="move-confirmation-files">
              <strong>Files to move</strong>
              <ul>
                {selectedImages.slice(0, MAX_CONFIRMATION_FILENAMES).map((image) => (
                  <li key={image.id} title={image.path}>{image.path}</li>
                ))}
              </ul>
              {selectedImages.length > MAX_CONFIRMATION_FILENAMES ? (
                <small>And {selectedImages.length - MAX_CONFIRMATION_FILENAMES} more file(s).</small>
              ) : null}
            </div>
            <p className="modal-warning permanent-warning">
              AntiDupeImage does not provide automatic restore.
            </p>
            {busy ? (
              <div className="modal-progress" role="status">
                Moving selected file(s) to duplicate folder...
              </div>
            ) : null}
            {moveIncomplete && !busy ? (
              <div className="modal-warning" role="alert">
                Move was not completed. Review the status message, then retry or cancel.
              </div>
            ) : null}
            <div className="modal-actions">
              <button
                ref={cancelMoveRef}
                className="secondary-button"
                disabled={busy}
                onClick={() => setConfirmOpen(false)}
              >
                Cancel
              </button>
              <button className="danger-button" disabled={busy || !canMove} onClick={confirmMove}>
                <FolderInput size={18} />
                <span>Move</span>
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}

function UnavailableThumb({ image }: { image: ImageSummary }) {
  return (
    <div className="thumb-unavailable">
      <ImageOff size={28} />
      <span>{statusLabel(image.status)}</span>
    </div>
  );
}

function EvidenceSummary({ group }: { group: DuplicateGroup }) {
  const rows = evidenceSummaryRows(group);
  return (
    <section className="evidence-summary" aria-label="Evidence summary">
      <div className="inspector-heading">
        <div>
          <span>Evidence summary</span>
          <strong>Why this group was formed</strong>
        </div>
        <span className="evidence-status">
          <Info size={15} />
          {group.reason === "exact-pixel" ? "Exact pixels can bypass visual verifier" : "Manual selection required"}
        </span>
      </div>
      <div className="evidence-table-wrap">
        <table>
          <thead>
            <tr>
              <th>Metric</th>
              <th>Observed</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.metric}>
                <td>{row.metric}</td>
                <td>{row.value}</td>
                <td>{row.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

type EvidenceSummaryRow = { metric: string; value: string; status: string };

function evidenceSummaryRows(group: DuplicateGroup): EvidenceSummaryRow[] {
  const evidences = group.images
    .map((image) => image.evidence)
    .filter((evidence): evidence is MatchEvidence => Boolean(evidence));
  if (evidences.length === 0) {
    return [
      {
        metric: "Group reason",
        value: group.reason,
        status: group.reason === "exact-pixel" ? "Exact decoded pixels" : "Verified"
      },
      { metric: "Minimum direct score", value: `${Math.round(group.score * 100)}%`, status: "Passed" },
      {
        metric: "Selection policy",
        value: group.reason === "exact-pixel" ? "Auto-selected except keep" : "Manual selection",
        status: "Safe"
      }
    ];
  }

  const rows: EvidenceSummaryRow[] = [
    {
      metric: "Candidate evidence",
      value: `${evidences.length} candidate${evidences.length === 1 ? "" : "s"}`,
      status: "Aggregated"
    }
  ];
  addEvidenceRangeRow(rows, "pHash", evidences, "phash_distance", "distance");
  addEvidenceRangeRow(rows, "dHash", evidences, "dhash_distance", "distance");
  addEvidenceRangeRow(rows, "wHash", evidences, "whash_distance", "distance");
  addEvidenceRangeRow(rows, "RGB/HSV histogram", evidences, "histogram_similarity", "percent");
  addEvidenceRangeRow(rows, "Luma layout", evidences, "layout_similarity", "percent");
  addEvidenceRangeRow(rows, "Structure", evidences, "structural_similarity", "percent");
  addEvidenceRangeRow(rows, "Edge layout", evidences, "edge_similarity", "percent");
  addEvidenceRangeRow(rows, "CLIP", evidences, "clip_similarity", "percent");
  addEvidenceRangeRow(rows, "Quality score", evidences, "quality_score", "percent");
  addFlagRow(rows, "Low information", evidences, "low_information", "Stricter verifier used");
  addFlagRow(rows, "Small difference", evidences, "small_difference", "Extra support required");
  rows.push({
    metric: "Selection policy",
    value: group.reason === "exact-pixel" ? "Exact candidates may be selected" : "Visual candidates start unselected",
    status: "Safe"
  });
  return rows;
}

function addEvidenceRangeRow(
  rows: EvidenceSummaryRow[],
  metric: string,
  evidences: MatchEvidence[],
  key: keyof MatchEvidence,
  kind: "distance" | "percent"
) {
  const values = evidences
    .map((evidence) => evidence[key])
    .filter((value): value is number => typeof value === "number");
  if (values.length === 0) return;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const observed =
    min === max
      ? formatEvidenceValue(min, kind)
      : `${formatEvidenceValue(min, kind)}-${formatEvidenceValue(max, kind)}`;
  rows.push({
    metric,
    value: observed,
    status:
      values.length === evidences.length
        ? `Passed across ${evidences.length} candidate${evidences.length === 1 ? "" : "s"}`
        : `Passed for ${values.length}/${evidences.length} candidates`
  });
}

function addFlagRow(
  rows: EvidenceSummaryRow[],
  metric: string,
  evidences: MatchEvidence[],
  key: keyof MatchEvidence,
  status: string
) {
  const count = evidences.filter((evidence) => evidence[key] === true).length;
  if (count === 0) return;
  rows.push({
    metric,
    value: count === evidences.length ? "All candidates" : `${count}/${evidences.length} candidates`,
    status
  });
}

function formatEvidenceValue(value: number, kind: "distance" | "percent"): string {
  return kind === "percent" ? `${Math.round(value * 100)}%` : String(value);
}

function ThumbnailImage({ image }: { image: ImageSummary }) {
  const [thumbnail, setThumbnail] = useState<{ url: string | null; error: string | null }>({
    url: null,
    error: null
  });

  useEffect(() => {
    let cancelled = false;
    let objectUrl: string | null = null;
    setThumbnail({ url: null, error: null });
    const request = fetchThumbnailLimited(image.id);
    void request.promise
      .then((blob) => {
        const nextUrl = URL.createObjectURL(blob);
        if (cancelled) {
          URL.revokeObjectURL(nextUrl);
          return;
        }
        objectUrl = nextUrl;
        setThumbnail({ url: nextUrl, error: null });
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setThumbnail({ url: null, error: messageFromError(error) });
        }
      });

    return () => {
      cancelled = true;
      request.cancel();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [image.id]);

  if (thumbnail.error) {
    return (
      <div className="thumb-unavailable">
        <ImageOff size={28} />
        <span title={thumbnail.error}>Thumbnail unavailable</span>
      </div>
    );
  }
  if (!thumbnail.url) {
    return <div className="thumb-loading">Loading thumbnail</div>;
  }
  return <img src={thumbnail.url} alt={basename(image.path)} loading="lazy" />;
}

function fetchThumbnailLimited(imageId: number): { promise: Promise<Blob>; cancel: () => void } {
  const cached = thumbnailBlobCache.get(imageId);
  if (cached) {
    thumbnailBlobCache.delete(imageId);
    thumbnailBlobCache.set(imageId, cached);
    return { promise: Promise.resolve(cached), cancel: () => undefined };
  }
  const shared = queuedThumbnailRequests.get(imageId) ?? activeThumbnailRequestsById.get(imageId);
  if (shared) {
    shared.consumers += 1;
    return {
      promise: shared.promise,
      cancel: () => releaseThumbnailTask(shared)
    };
  }
  const task: ThumbnailTask = {
    imageId,
    promise: Promise.resolve(new Blob()),
    resolve: () => undefined,
    reject: () => undefined,
    controller: new AbortController(),
    cancelled: false,
    started: false,
    consumers: 1
  };
  const promise = new Promise<Blob>((resolve, reject) => {
    task.resolve = resolve;
    task.reject = reject;
  });
  task.promise = promise;
  thumbnailQueue.push(task);
  queuedThumbnailRequests.set(imageId, task);
  pumpThumbnailQueue();
  return {
    promise,
    cancel: () => releaseThumbnailTask(task)
  };
}

function releaseThumbnailTask(task: ThumbnailTask) {
  if (task.cancelled) return;
  task.consumers = Math.max(0, task.consumers - 1);
  if (task.consumers > 0) return;
  cancelThumbnailTask(task);
}

function cancelThumbnailTask(task: ThumbnailTask) {
  if (task.cancelled) return;
  task.cancelled = true;
  task.consumers = 0;
  queuedThumbnailRequests.delete(task.imageId);
  activeThumbnailRequestsById.delete(task.imageId);
  if (task.started) {
    task.controller.abort();
    task.reject(new Error("Thumbnail request cancelled"));
  } else {
    task.reject(new Error("Thumbnail request cancelled"));
    pumpThumbnailQueue();
  }
}

export function clearThumbnailCache() {
  thumbnailBlobCache.clear();
  const tasks = new Set([...queuedThumbnailRequests.values(), ...activeThumbnailRequestsById.values()]);
  for (const task of tasks) {
    cancelThumbnailTask(task);
  }
  queuedThumbnailRequests.clear();
  activeThumbnailRequestsById.clear();
  thumbnailQueue.length = 0;
}

export function getThumbnailCacheSize(): number {
  return thumbnailBlobCache.size;
}

function pumpThumbnailQueue() {
  while (activeThumbnailRequests < MAX_THUMBNAIL_REQUESTS && thumbnailQueue.length > 0) {
    const task = thumbnailQueue.shift();
    if (!task) return;
    if (task.cancelled) continue;
    queuedThumbnailRequests.delete(task.imageId);
    activeThumbnailRequestsById.set(task.imageId, task);
    task.started = true;
    activeThumbnailRequests += 1;
    void fetchThumbnail(task.imageId, task.controller.signal)
      .then((blob) => {
        if (task.cancelled) return;
        thumbnailBlobCache.delete(task.imageId);
        thumbnailBlobCache.set(task.imageId, blob);
        while (thumbnailBlobCache.size > MAX_THUMBNAIL_CACHE_ITEMS) {
          const oldest = thumbnailBlobCache.keys().next().value;
          if (oldest === undefined) break;
          thumbnailBlobCache.delete(oldest);
        }
        task.resolve(blob);
      }, (error: unknown) => {
        if (!task.cancelled) task.reject(error);
      })
      .finally(() => {
        activeThumbnailRequests -= 1;
        activeThumbnailRequestsById.delete(task.imageId);
        pumpThumbnailQueue();
      });
  }
}

function EvidenceChips({ image }: { image: ImageSummary }) {
  const items = evidenceItems(image);
  if (items.length === 0) return null;
  return (
    <div className="evidence-row" aria-label={`Evidence for ${basename(image.path)}`}>
      {items.map((item) => (
        <span key={item.label} title={item.title ?? item.label}>
          {item.label}
        </span>
      ))}
    </div>
  );
}

function evidenceItems(image: ImageSummary): Array<{ label: string; title?: string }> {
  const evidence = image.evidence;
  if (!evidence) return [];
  const items: Array<{ label: string; title?: string }> = [];
  if (evidence.match_type === "exact-pixel") {
    items.push({ label: "Exact", title: "Exact decoded pixel match" });
  }
  if (evidence.precision_mode) {
    items.push({ label: modeLabel(evidence.precision_mode) });
  }
  if (evidence.performance_profile === "lightweight") {
    items.push({ label: "Lightweight", title: "Matched with reduced lightweight evidence" });
  }
  if (evidence.reduced_evidence) {
    items.push({ label: "Reduced", title: "Reduced-resolution evidence" });
  }
  if (evidence.candidate_limited) {
    items.push({ label: "Limited", title: "Candidate list was capped for speed" });
  }
  if (evidence.low_information) {
    items.push({ label: "Low info", title: "Low-information image required stricter checks" });
  }
  if (evidence.small_difference) {
    items.push({ label: "Small diff", title: "Small local difference detected with strict evidence" });
  }
  addNumberItem(items, "pHash", evidence.phash_distance, "distance");
  addNumberItem(items, "dHash", evidence.dhash_distance, "distance");
  addNumberItem(items, "wHash", evidence.whash_distance, "distance");
  addPercentItem(items, "Layout", evidence.layout_similarity);
  addPercentItem(items, "Structure", evidence.structural_similarity);
  addPercentItem(items, "Edge", evidence.edge_similarity);
  addPercentItem(items, "Color", evidence.histogram_similarity);
  addPercentItem(items, "CLIP", evidence.clip_similarity);
  addPercentItem(items, "Quality", evidence.quality_score);
  return items;
}

function addNumberItem(
  items: Array<{ label: string; title?: string }>,
  label: string,
  value: number | undefined,
  suffix: string
) {
  if (typeof value !== "number") return;
  items.push({ label: `${label} ${value}`, title: `${label} ${suffix}: ${value}` });
}

function addPercentItem(
  items: Array<{ label: string; title?: string }>,
  label: string,
  value: number | undefined
) {
  if (typeof value !== "number") return;
  items.push({ label: `${label} ${Math.round(value * 100)}%` });
}

function modeLabel(mode: string): string {
  if (mode === "exact_pixel") return "Exact pixel";
  if (mode === "ultra_strict") return "Ultra strict";
  if (mode === "conservative") return "Conservative";
  if (mode === "balanced") return "Balanced";
  if (mode === "broad") return "Broad";
  return mode;
}

function defaultMoveSelection(group: DuplicateGroup, keepId: number | null): Set<number> {
  if (keepId === null) return new Set();
  if (group.reason !== "exact-pixel") return new Set();
  return new Set(
    group.images
      .filter((image) => image.available && image.id !== keepId)
      .map((image) => image.id)
  );
}

function basename(path: string): string {
  return path.split(/[\\/]/).pop() ?? path;
}

function statusLabel(status: ImageSummary["status"]): string {
  if (status === "available") return "Available";
  if (status === "moved" || status === "trashed") return "Moved to duplicate folder";
  if (status === "missing") return "Missing from original location";
  if (status === "changed") return "Changed after scan";
  return "Unsafe to move";
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function messageFromError(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
