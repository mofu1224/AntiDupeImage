import { ChevronLeft, ChevronRight, Images, SearchX } from "lucide-react";
import type { GroupFilter, GroupSort, GroupSummary } from "../types/api";

export type { GroupFilter, GroupSort };

type Props = {
  groups: GroupSummary[];
  total: number;
  limit: number;
  offset: number;
  selectedId?: number;
  filter: GroupFilter;
  sort: GroupSort;
  loading: boolean;
  error: string | null;
  onFilterChange: (filter: GroupFilter) => void;
  onSortChange: (sort: GroupSort) => void;
  onSelect: (group: GroupSummary) => void;
  onPage: (offset: number) => void;
  onRetry: () => void;
};

export function GroupList({
  groups,
  total,
  limit,
  offset,
  selectedId,
  filter,
  sort,
  loading,
  error,
  onFilterChange,
  onSortChange,
  onSelect,
  onPage,
  onRetry
}: Props) {
  const hasPrevious = offset > 0;
  const hasNext = offset + groups.length < total;

  return (
    <section className="panel group-list-panel">
      <div className="panel-header">
        <div>
          <h2>Duplicate groups</h2>
          <p>{total} groups</p>
        </div>
        <Images size={22} />
      </div>
      <div className="group-toolbar">
        <label>
          <span>Group filter</span>
          <select
            aria-label="Group filter"
            value={filter}
            onChange={(event) => onFilterChange(event.target.value as GroupFilter)}
          >
            <option value="all">All groups</option>
            <option value="visual">Visual matches</option>
            <option value="exact">Exact matches</option>
          </select>
        </label>
        <label>
          <span>Sort by</span>
          <select
            aria-label="Sort groups"
            value={sort}
            onChange={(event) => onSortChange(event.target.value as GroupSort)}
          >
            <option value="recommended">Recommended keep</option>
            <option value="score">Best match score</option>
            <option value="files">File count</option>
          </select>
        </label>
      </div>
      <div className="group-list" aria-label="Duplicate groups">
        {error ? (
          <div className="empty-state">
            <SearchX size={28} />
            <span>{error}</span>
            <button className="secondary-button" onClick={onRetry}>
              Retry
            </button>
          </div>
        ) : loading && groups.length === 0 ? (
          <div className="empty-state">
            <Images size={28} className="spin" />
            <span>Loading duplicate groups</span>
          </div>
        ) : groups.length === 0 ? (
          <div className="empty-state">
            <SearchX size={28} />
            <span>No duplicate groups</span>
          </div>
        ) : (
          groups.map((group) => {
            const remaining = Math.max(0, group.image_count - group.moved_count);
            const selectedType = group.reason === "exact-pixel" ? "Exact match" : "Visual match";
            return (
              <button
                key={group.id}
                className={`group-row ${selectedId === group.id ? "active" : ""}`}
                onClick={() => onSelect(group)}
                aria-pressed={selectedId === group.id}
              >
                <span className="group-row-title">Group {group.id}</span>
                <span className="group-type">{selectedType}</span>
                <span>
                  {remaining}/{group.image_count} active
                </span>
                <strong>{Math.round(group.score * 100)}%</strong>
                <small title={group.recommended_keep_path}>
                  {basename(group.recommended_keep_path)}
                </small>
              </button>
            );
          })
        )}
      </div>
      {total > limit ? (
        <div className="pager">
          <button
            className="icon-button"
            disabled={!hasPrevious}
            onClick={() => onPage(Math.max(0, offset - limit))}
            title="Previous group page"
            aria-label="Previous group page"
          >
            <ChevronLeft size={16} />
          </button>
          <span>
            {offset + 1}-{Math.min(offset + groups.length, total)} / {total}
          </span>
          <button
            className="icon-button"
            disabled={!hasNext}
            onClick={() => onPage(offset + limit)}
            title="Next group page"
            aria-label="Next group page"
          >
            <ChevronRight size={16} />
          </button>
        </div>
      ) : null}
    </section>
  );
}

function basename(path: string): string {
  return path.split(/[\\/]/).pop() ?? path;
}
