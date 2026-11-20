import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  CSSProperties,
  KeyboardEvent as ReactKeyboardEvent,
  PointerEvent as ReactPointerEvent
} from "react";

export const COMPARE_PANEL_WIDTH = { min: 390, max: 760, defaultValue: 520, step: 20 };
export const REVIEW_SURFACE_HEIGHT = { min: 440, max: 1100, defaultValue: 620, step: 32 };

type LayoutSettings = {
  comparePanel: number;
  reviewSurface: number;
};

type ResizeTarget = keyof LayoutSettings;
type LayoutCssVariable = "--compare-panel-width" | "--review-surface-height";
type LayoutStyle = CSSProperties & Record<LayoutCssVariable, string>;

const LAYOUT_STORAGE_KEY = "antidupeimage.layout.v1";
const LAYOUT_LIMITS: Record<ResizeTarget, { min: number; max: number; defaultValue: number; step: number }> = {
  comparePanel: COMPARE_PANEL_WIDTH,
  reviewSurface: REVIEW_SURFACE_HEIGHT
};
const LAYOUT_CSS_VARIABLES: Record<ResizeTarget, LayoutCssVariable> = {
  comparePanel: "--compare-panel-width",
  reviewSurface: "--review-surface-height"
};

export function useWorkspaceLayout() {
  const [layoutSettings, setLayoutSettings] = useState<LayoutSettings>(() => loadLayoutSettings());
  const [resizeTarget, setResizeTarget] = useState<ResizeTarget | null>(null);
  const workspaceRef = useRef<HTMLDivElement | null>(null);
  const reviewSurfaceRef = useRef<HTMLDivElement | null>(null);
  const layoutSettingsRef = useRef(layoutSettings);
  const resizeCleanupRef = useRef<(() => void) | null>(null);

  const applyTransientLayoutValue = useCallback((target: ResizeTarget, value: number) => {
    const nextValue = clampLayoutValue(value, LAYOUT_LIMITS[target]);
    if (layoutSettingsRef.current[target] === nextValue) return;
    layoutSettingsRef.current = { ...layoutSettingsRef.current, [target]: nextValue };
    workspaceRef.current?.style.setProperty(LAYOUT_CSS_VARIABLES[target], `${nextValue}px`);
  }, []);

  const commitLayoutSettings = useCallback(() => {
    setLayoutSettings((current) =>
      layoutSettingsEqual(current, layoutSettingsRef.current) ? current : { ...layoutSettingsRef.current }
    );
  }, []);

  const commitLayoutValue = useCallback((target: ResizeTarget, value: number) => {
    const nextValue = clampLayoutValue(value, LAYOUT_LIMITS[target]);
    setLayoutSettings((current) => {
      if (current[target] === nextValue) return current;
      const next = { ...current, [target]: nextValue };
      layoutSettingsRef.current = next;
      workspaceRef.current?.style.setProperty(LAYOUT_CSS_VARIABLES[target], `${nextValue}px`);
      return next;
    });
  }, []);

  const startResize = useCallback(
    (target: ResizeTarget, event: ReactPointerEvent<HTMLButtonElement>) => {
      event.preventDefault();
      event.currentTarget.focus();
      event.currentTarget.setPointerCapture?.(event.pointerId);
      resizeCleanupRef.current?.();

      function onPointerMove(pointerEvent: PointerEvent) {
        pointerEvent.preventDefault();
        if (target === "comparePanel") {
          const rect = reviewSurfaceRef.current?.getBoundingClientRect();
          if (!rect) return;
          applyTransientLayoutValue("comparePanel", rect.right - pointerEvent.clientX);
          return;
        }
        const rect = reviewSurfaceRef.current?.getBoundingClientRect();
        if (!rect) return;
        applyTransientLayoutValue("reviewSurface", pointerEvent.clientY - rect.top);
      }

      function stopResize() {
        resizeCleanupRef.current?.();
        resizeCleanupRef.current = null;
        commitLayoutSettings();
        setResizeTarget(null);
      }

      window.addEventListener("pointermove", onPointerMove);
      window.addEventListener("pointerup", stopResize, { once: true });
      window.addEventListener("pointercancel", stopResize, { once: true });
      resizeCleanupRef.current = () => {
        window.removeEventListener("pointermove", onPointerMove);
        window.removeEventListener("pointerup", stopResize);
        window.removeEventListener("pointercancel", stopResize);
      };
      setResizeTarget(target);
    },
    [applyTransientLayoutValue, commitLayoutSettings]
  );

  const resetLayoutWidth = useCallback(
    (target: ResizeTarget) => {
      commitLayoutValue(target, LAYOUT_LIMITS[target].defaultValue);
    },
    [commitLayoutValue]
  );

  const handleResizeKey = useCallback(
    (
      event: ReactKeyboardEvent<HTMLButtonElement>,
      target: ResizeTarget,
      decreaseKey: string,
      increaseKey: string
    ) => {
      const limits = LAYOUT_LIMITS[target];
      if (event.key === decreaseKey) {
        event.preventDefault();
        commitLayoutValue(target, layoutSettingsRef.current[target] - limits.step);
      } else if (event.key === increaseKey) {
        event.preventDefault();
        commitLayoutValue(target, layoutSettingsRef.current[target] + limits.step);
      } else if (event.key === "Home") {
        event.preventDefault();
        commitLayoutValue(target, limits.min);
      } else if (event.key === "End") {
        event.preventDefault();
        commitLayoutValue(target, limits.max);
      }
    },
    [commitLayoutValue]
  );

  useEffect(() => {
    layoutSettingsRef.current = layoutSettings;
    applyLayoutVariables(workspaceRef.current, layoutSettings);
  }, [layoutSettings]);

  useEffect(() => {
    try {
      window.localStorage.setItem(LAYOUT_STORAGE_KEY, JSON.stringify(layoutSettings));
    } catch {
      // Layout persistence is a convenience; the app must still work without localStorage.
    }
  }, [layoutSettings]);

  useEffect(() => {
    return () => {
      resizeCleanupRef.current?.();
      resizeCleanupRef.current = null;
    };
  }, []);

  const layoutStyle = useMemo<LayoutStyle>(
    () => ({
      "--compare-panel-width": `${layoutSettings.comparePanel}px`,
      "--review-surface-height": `${layoutSettings.reviewSurface}px`
    }),
    [layoutSettings.comparePanel, layoutSettings.reviewSurface]
  );
  const resizingClass = resizeTarget
    ? resizeTarget === "reviewSurface"
      ? " is-resizing is-resizing-y"
      : " is-resizing is-resizing-x"
    : "";

  return {
    handleResizeKey,
    layoutSettings,
    layoutStyle,
    resetLayoutWidth,
    resizingClass,
    reviewSurfaceRef,
    startResize,
    workspaceRef
  };
}

function loadLayoutSettings(): LayoutSettings {
  const fallback = {
    comparePanel: COMPARE_PANEL_WIDTH.defaultValue,
    reviewSurface: REVIEW_SURFACE_HEIGHT.defaultValue
  };
  try {
    const raw = window.localStorage.getItem(LAYOUT_STORAGE_KEY);
    if (!raw) return fallback;
    const parsed = JSON.parse(raw) as Partial<LayoutSettings>;
    return {
      comparePanel: clampLayoutValue(parsed.comparePanel, COMPARE_PANEL_WIDTH),
      reviewSurface: clampLayoutValue(parsed.reviewSurface, REVIEW_SURFACE_HEIGHT)
    };
  } catch {
    return fallback;
  }
}

function clampLayoutValue(
  value: unknown,
  limits: { min: number; max: number; defaultValue: number }
): number {
  if (typeof value !== "number" || !Number.isFinite(value)) return limits.defaultValue;
  return Math.max(limits.min, Math.min(limits.max, Math.round(value)));
}

function applyLayoutVariables(element: HTMLElement | null, settings: LayoutSettings) {
  if (!element) return;
  for (const target of Object.keys(LAYOUT_CSS_VARIABLES) as ResizeTarget[]) {
    element.style.setProperty(LAYOUT_CSS_VARIABLES[target], `${settings[target]}px`);
  }
}

function layoutSettingsEqual(left: LayoutSettings, right: LayoutSettings): boolean {
  return (Object.keys(LAYOUT_CSS_VARIABLES) as ResizeTarget[]).every((target) => left[target] === right[target]);
}
