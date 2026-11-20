import { Ban, ChevronLeft, ChevronRight, History, ListRestart, RefreshCw, Trash2 } from "lucide-react";
import type { ScanState, ScanStatus } from "../types/api";
import { formatLocalTimestamp, parseApiUtcTimestamp } from "../utils/dateTime";

export type ScanHistoryFilter = "all" | ScanStatus;

type Props = {
  scans: ScanState[];
  total: number;
  limit: number;
  offset: number;
  selectedId?: number;
  filter: ScanHistoryFilter;
  busy: boolean;
  onSelect: (scan: ScanState) => void;
  onReuse: (scan: ScanState) => void;
  onCancel: (scan: ScanState) => void;
  onDelete: (scan: ScanState) => void;
  onRefresh: () => void;
  onPage: (offset: number) => void;
  onFilter: (filter: ScanHistoryFilter) => void;
};

export function ScanHistory({
  scans,
  total,
  limit,
  offset,
  selectedId,
  filter,
  busy,
  onSelect,
  onReuse,
  onCancel,
  onDelete,
  onRefresh,
  onPage,
  onFilter
}: Props) {
  const hasPrevious = offset > 0;
  const hasNext = offset + scans.length < total;
  return (
    <section className="panel history-panel">
      <div className="panel-header">
        <div>
          <h2>History</h2>
          <p>{total} scans</p>
        </div>
        <button
          className="icon-button"
          onClick={onRefresh}
          disabled={busy}
          title="Refresh scan history"
          aria-label="Refresh scan history"
        >
          <RefreshCw size={18} />
        </button>
      </div>
      <div className="history-toolbar">
        <label className="field">
          <span>History status</span>
          <select
            value={filter}
            disabled={busy}
            onChange={(event) => onFilter(event.target.value as ScanHistoryFilter)}
          >
            <option value="all">All scans</option>
            <option value="completed">Completed</option>
            <option value="failed">Failed</option>
            <option value="cancelled">Cancelled</option>
          </select>
        </label>
      </div>
      <div className="history-list">
        {scans.length === 0 ? (
          <div className="empty-state compact">
            <History size={24} />
            <span>No scan history</span>
          </div>
        ) : (
          scans.map((scan) => (
            <div className={`history-row ${scan.id === selectedId ? "active" : ""}`} key={scan.id}>
              <button
                className="history-select"
                onClick={() => onSelect(scan)}
                aria-pressed={scan.id === selectedId}
              >
                <strong>Scan #{scan.id}</strong>
                <span>
                  {scan.phase ?? scan.status} / {profileLabel(scan.thresholds.performance_profile)}
                </span>
                <small title={scan.path}>{scan.path}</small>
                <div className="history-metrics">
                  <span>{formatLocalTimestamp(scan.created_at)}</span>
                  <span>{formatDuration(scan.created_at, scan.updated_at)}</span>
                  <span>{scan.total_files} files</span>
                  <span>{scan.succeeded_files} succeeded</span>
                  <span>{scan.failed_files} failed</span>
                  <span>{scan.skipped_files} skipped</span>
                  <span>{scan.duplicate_groups} groups</span>
                  <span>{precisionLabel(scan.thresholds.precision_mode)}</span>
                </div>
                {scan.progress_detail ? <small className="history-detail">{scan.progress_detail}</small> : null}
              </button>
              <div className="history-actions">
                <button
                  type="button"
                  className="history-reuse-button"
                  onClick={() => onReuse(scan)}
                  disabled={busy}
                  title="Load stored settings without starting a scan"
                  aria-label={`Use settings from scan ${scan.id}`}
                >
                  <ListRestart size={15} />
                  <span>Use settings</span>
                </button>
                {scan.can_cancel ? (
                  <button
                    className="icon-button"
                    onClick={() => onCancel(scan)}
                    disabled={busy}
                    title="Cancel scan"
                    aria-label={`Cancel scan ${scan.id}`}
                  >
                    <Ban size={15} />
                  </button>
                ) : (
                  <button
                    className="icon-button"
                    onClick={() => onDelete(scan)}
                    disabled={busy}
                    title="Delete scan history"
                    aria-label={`Delete scan ${scan.id} history`}
                  >
                    <Trash2 size={15} />
                  </button>
                )}
              </div>
            </div>
          ))
        )}
      </div>
      {total > limit ? (
        <div className="pager">
          <button
            className="icon-button"
            disabled={!hasPrevious || busy}
            onClick={() => onPage(Math.max(0, offset - limit))}
            title="Previous scan page"
            aria-label="Previous scan page"
          >
            <ChevronLeft size={16} />
          </button>
          <span>
            {offset + 1}-{Math.min(offset + scans.length, total)} / {total}
          </span>
          <button
            className="icon-button"
            disabled={!hasNext || busy}
            onClick={() => onPage(offset + limit)}
            title="Next scan page"
            aria-label="Next scan page"
          >
            <ChevronRight size={16} />
          </button>
        </div>
      ) : null}
    </section>
  );
}

function profileLabel(profile: string | undefined): string {
  if (profile === "lightweight") return "Lightweight";
  return "Standard";
}

function precisionLabel(precision: string | undefined): string {
  if (!precision) return "Conservative";
  return precision
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function formatDuration(createdAt: string, updatedAt: string): string {
  const start = parseApiUtcTimestamp(createdAt);
  const end = parseApiUtcTimestamp(updatedAt);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return "Duration unavailable";
  const seconds = Math.max(0, Math.round((end - start) / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;
  return `${minutes}m ${remainingSeconds}s`;
}
