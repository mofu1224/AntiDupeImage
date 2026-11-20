import { useCallback, useEffect, useRef, useState } from "react";

export type ActionProgress = {
  label: string;
  detail: string;
  progress: number;
  status: "running" | "done" | "partial" | "failed";
};

export function useActionProgress() {
  const [actionProgress, setActionProgress] = useState<ActionProgress | null>(null);
  const actionProgressTimerRef = useRef<number | null>(null);

  const clearActionProgressTimer = useCallback(() => {
    if (actionProgressTimerRef.current !== null) {
      window.clearTimeout(actionProgressTimerRef.current);
      actionProgressTimerRef.current = null;
    }
  }, []);

  const showActionProgress = useCallback(
    (label: string, detail: string, progress: number) => {
      clearActionProgressTimer();
      setActionProgress({
        label,
        detail,
        progress: clampProgress(progress),
        status: "running"
      });
    },
    [clearActionProgressTimer]
  );

  const finishActionProgress = useCallback(
    (label: string, detail: string) => {
      clearActionProgressTimer();
      setActionProgress({
        label,
        detail,
        progress: 1,
        status: "done"
      });
      actionProgressTimerRef.current = window.setTimeout(() => setActionProgress(null), 3500);
    },
    [clearActionProgressTimer]
  );

  const finishPartialActionProgress = useCallback(
    (label: string, detail: string) => {
      clearActionProgressTimer();
      setActionProgress({
        label,
        detail,
        progress: 1,
        status: "partial"
      });
    },
    [clearActionProgressTimer]
  );

  const failActionProgress = useCallback(
    (label: string, detail: string) => {
      clearActionProgressTimer();
      setActionProgress({
        label,
        detail,
        progress: 1,
        status: "failed"
      });
    },
    [clearActionProgressTimer]
  );

  useEffect(() => clearActionProgressTimer, [clearActionProgressTimer]);

  return {
    actionProgress,
    clearActionProgressTimer,
    failActionProgress,
    finishActionProgress,
    finishPartialActionProgress,
    showActionProgress
  };
}

function clampProgress(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(1, value));
}
