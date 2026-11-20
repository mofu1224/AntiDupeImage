import { AlertTriangle, ChevronLeft, ChevronRight, Copy, ListRestart } from "lucide-react";
import { useState } from "react";
import type { ScanFailure } from "../types/api";
import { formatLocalTimestamp } from "../utils/dateTime";

type Props = {
  failures: ScanFailure[];
  total: number;
  limit: number;
  offset: number;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  onPage: (offset: number) => void;
  onReuseSettings?: () => void;
};

export function FailureList({
  failures,
  total,
  limit,
  offset,
  loading,
  error,
  onRetry,
  onPage,
  onReuseSettings
}: Props) {
  const [copyStatus, setCopyStatus] = useState<string | null>(null);
  if (total === 0 && !loading && !error) return null;
  const hasPrevious = offset > 0;
  const hasNext = offset + failures.length < total;

  async function copyCurrentPage() {
    const text = failures
      .map((failure) => `[${failure.stage}] ${failure.path}\n${failure.error}\n${failure.created_at}`)
      .join("\n\n");
    try {
      if (!navigator.clipboard) throw new Error("Clipboard access is unavailable");
      await navigator.clipboard.writeText(text);
      setCopyStatus(`Copied ${failures.length} issue(s) from this page.`);
    } catch {
      setCopyStatus("Could not copy issue details. Select and copy the expanded text instead.");
    }
  }

  return (
    <section className="panel failure-panel">
      <div className="panel-header">
        <div>
          <h2>Failures</h2>
          <p>{loading ? "Loading recorded issues" : `${total} recorded issues`}</p>
        </div>
        <div className="failure-header-actions">
          {onReuseSettings ? (
            <button className="secondary-button" onClick={onReuseSettings} disabled={loading}>
              <ListRestart size={16} />
              <span>Use scan settings</span>
            </button>
          ) : null}
          <button
            className="secondary-button"
            onClick={() => void copyCurrentPage()}
            disabled={loading || failures.length === 0}
          >
            <Copy size={16} />
            <span>Copy this page</span>
          </button>
          <AlertTriangle size={20} />
        </div>
      </div>
      {copyStatus ? <p className="failure-copy-status" role="status">{copyStatus}</p> : null}
      <div className="failure-list">
        {error ? (
          <div className="empty-state compact">
            <span>{error}</span>
            <button className="secondary-button" onClick={onRetry}>
              Retry
            </button>
          </div>
        ) : loading && failures.length === 0 ? (
          <div className="empty-state compact">
            <span>Loading recorded issues</span>
          </div>
        ) : (
          failures.map((failure) => (
            <details className="failure-row" key={failure.id}>
              <summary>
                <strong>{failure.stage}</strong>
                <span title={failure.path}>{failure.path}</span>
                <small>{failureSummary(failure.error)}</small>
                <time dateTime={failure.created_at}>{formatLocalTimestamp(failure.created_at)}</time>
              </summary>
              <div className="failure-detail">
                <strong>Full error</strong>
                <p>{failure.error}</p>
              </div>
            </details>
          ))
        )}
      </div>
      {total > limit ? (
        <div className="pager">
          <button
            className="icon-button"
            disabled={!hasPrevious || loading}
            onClick={() => onPage(Math.max(0, offset - limit))}
            title="Previous failure page"
            aria-label="Previous failure page"
          >
            <ChevronLeft size={16} />
          </button>
          <span>
            {offset + 1}-{Math.min(offset + failures.length, total)} / {total}
          </span>
          <button
            className="icon-button"
            disabled={!hasNext || loading}
            onClick={() => onPage(offset + limit)}
            title="Next failure page"
            aria-label="Next failure page"
          >
            <ChevronRight size={16} />
          </button>
        </div>
      ) : null}
    </section>
  );
}

function failureSummary(error: string): string {
  const firstLine = error.split(/\r?\n/, 1)[0].trim();
  return firstLine.length > 140 ? `${firstLine.slice(0, 137)}...` : firstLine;
}
