import { AlertTriangle, CheckCircle2, Images, Loader2 } from "lucide-react";
import type { ScanState } from "../types/api";

type Props = {
  scan: ScanState | null;
  onViewDuplicates?: () => void;
};

export function ProgressPanel({ scan, onViewDuplicates }: Props) {
  const phase = scan?.phase ?? scan?.status ?? "-";
  const detail = scan?.progress_detail?.trim();
  const distinctDetail = detail && detail !== scan?.error ? detail : null;
  const statusIcon =
    scan?.status === "failed" || scan?.status === "cancelled" ? (
      <AlertTriangle size={20} />
    ) : scan?.status === "completed" ? (
      <CheckCircle2 size={20} />
    ) : (
      <Loader2 size={20} className={scan ? "spin" : ""} />
    );

  return (
    <section className="panel progress-panel">
      <div className="panel-header">
        <div>
          <h2>Progress</h2>
          {scan ? <p>Scan #{scan.id} / {phase}</p> : null}
        </div>
        {statusIcon}
      </div>
      <div
        className="progress-bar"
        role="progressbar"
        aria-label={scan ? `Scan ${scan.id} progress` : "Scan progress"}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round((scan?.progress ?? 0) * 100)}
      >
        <div style={{ width: `${Math.round((scan?.progress ?? 0) * 100)}%` }} />
      </div>
      {scan ? <div className="metric-row">
        <Metric label="Status" value={scan?.status ?? "-"} />
        <Metric label="Processed" value={`${scan?.processed_files ?? 0}/${scan?.total_files ?? 0}`} />
        {scan.failed_files > 0 ? <Metric label="Failed" value={String(scan.failed_files)} /> : null}
        {scan.skipped_files > 0 ? <Metric label="Skipped" value={String(scan.skipped_files)} /> : null}
        <Metric label="Groups" value={String(scan?.duplicate_groups ?? 0)} />
      </div> : (
        <div className="progress-idle">
          <Images size={32} strokeWidth={1.25} />
          <span>待機中</span>
        </div>
      )}
      {distinctDetail ? <div className="progress-detail">{distinctDetail}</div> : null}
      {scan?.status === "completed" && scan.duplicate_groups === 0 ? (
        <div className="progress-empty-result">
          <CheckCircle2 size={18} />
          <span>No duplicate groups were found in this scan.</span>
        </div>
      ) : null}
      {scan?.status === "completed" && scan.duplicate_groups > 0 && onViewDuplicates ? (
        <div className="progress-result-action">
          <div>
            <strong>{scan.duplicate_groups} duplicate group(s) ready to review</strong>
          </div>
          <button className="primary-button" onClick={onViewDuplicates}>
            <Images size={17} />
            <span>Review duplicates</span>
          </button>
        </div>
      ) : null}
      {scan?.warnings.map((warning) => (
        <div className="warning-box" role="status" key={warning}>
          {warning}
        </div>
      ))}
      {scan?.error ? <div className="error-box" role="alert">{scan.error}</div> : null}
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
