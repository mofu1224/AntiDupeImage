import { ChevronDown, FolderOpen, FolderSearch, Play, SlidersHorizontal } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import { chooseFolder } from "../api/client";
import type {
  PerformanceProfile,
  PrecisionMode,
  ScanRequest,
  SystemResponse,
  Thresholds
} from "../types/api";

type Props = {
  system: SystemResponse | null;
  busy: boolean;
  template?: ScanFormTemplate | null;
  onSubmit: (request: ScanRequest) => void;
};

export type ScanFormTemplate = {
  sourceScanId: number;
  revision: number;
  request: Omit<ScanRequest, "allow_clip_fallback">;
};

type NumericThresholds = Pick<Thresholds, "phash_distance" | "histogram_similarity" | "clip_similarity">;

const thresholdPresets: Record<PrecisionMode, NumericThresholds> = {
  exact_pixel: {
    phash_distance: 0,
    histogram_similarity: 1,
    clip_similarity: 1
  },
  ultra_strict: {
    phash_distance: 2,
    histogram_similarity: 0.99,
    clip_similarity: 0.99
  },
  conservative: {
    phash_distance: 10,
    histogram_similarity: 0.9,
    clip_similarity: 0.92
  },
  balanced: {
    phash_distance: 12,
    histogram_similarity: 0.86,
    clip_similarity: 0.92
  },
  broad: {
    phash_distance: 16,
    histogram_similarity: 0.78,
    clip_similarity: 0.88
  }
};

const defaultThresholds: Thresholds = {
  ...thresholdPresets.conservative,
  clip_enabled: false,
  precision_mode: "conservative",
  performance_profile: "standard"
};

const precisionOptions: Array<{ value: PrecisionMode; label: string }> = [
  { value: "exact_pixel", label: "Exact pixel" },
  { value: "ultra_strict", label: "Ultra strict" },
  { value: "conservative", label: "Conservative" },
  { value: "balanced", label: "Balanced" },
  { value: "broad", label: "Broad" }
];

const performanceOptions: Array<{ value: PerformanceProfile; label: string }> = [
  { value: "standard", label: "Standard accuracy" },
  { value: "lightweight", label: "Lightweight fast" }
];

export function ScanForm({ system, busy, template = null, onSubmit }: Props) {
  const [path, setPath] = useState("");
  const [recursive, setRecursive] = useState(true);
  const [device, setDevice] = useState("auto");
  const [gpuIndex, setGpuIndex] = useState<number | null>(null);
  const [model, setModel] = useState("");
  const [pretrained, setPretrained] = useState("");
  const [allowClipFallback, setAllowClipFallback] = useState(true);
  const [thresholds, setThresholds] = useState<Thresholds>(defaultThresholds);
  const [pickerBusy, setPickerBusy] = useState(false);
  const [pickerError, setPickerError] = useState<string | null>(null);
  const [deviceFallbackNotice, setDeviceFallbackNotice] = useState<string | null>(null);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const advancedSettingsId = useId();

  const devices = useMemo(() => {
    const available = system?.device.available ?? ["cpu"];
    const cudaAlias = available.some((item) => item.startsWith("cuda:")) ? ["cuda"] : [];
    return Array.from(new Set(["auto", ...cudaAlias, ...available, device]));
  }, [device, system]);
  const clipAvailable = system?.clip.available ?? false;
  const usesCudaIndex = device === "cuda";
  const lightweight = thresholds.performance_profile === "lightweight";
  const deviceCandidate = usesCudaIndex ? `cuda:${gpuIndex ?? 0}` : device;
  const reusedDeviceNeedsValidation =
    template !== null &&
    deviceCandidate !== "auto" &&
    (!system || !system.device.available.includes(deviceCandidate));
  const startDisabledReason = busy
    ? "Wait for the current action to finish."
    : !path.trim()
      ? "Choose a folder or enter a local image path to start scanning."
      : reusedDeviceNeedsValidation
        ? system
          ? "The device saved in this scan is unavailable. Choose Auto or another available device."
          : "Waiting for system status to validate the reused device."
        : null;

  useEffect(() => {
    if (!template) return;
    const request = template.request;
    setPath(request.path);
    setRecursive(request.recursive);
    setDevice(request.device);
    setGpuIndex(request.gpu_index ?? null);
    setModel(request.model ?? "");
    setPretrained(request.pretrained ?? "");
    setThresholds({ ...request.thresholds });
    setPickerError(null);
    setDeviceFallbackNotice(null);
    setAdvancedOpen(true);
    window.requestAnimationFrame(() => {
      headingRef.current?.scrollIntoView?.({ behavior: "smooth", block: "start" });
      headingRef.current?.focus();
    });
  }, [template]);

  useEffect(() => {
    if (system && !system.clip.available && thresholds.clip_enabled) {
      setThresholds((current) => ({ ...current, clip_enabled: false }));
    }
  }, [system, thresholds.clip_enabled]);

  useEffect(() => {
    if (!system) return;
    if (deviceCandidate !== "auto" && !system.device.available.includes(deviceCandidate)) {
      setDevice("auto");
      setGpuIndex(null);
      setDeviceFallbackNotice(
        `Device ${deviceCandidate} is unavailable on this system, so Auto was selected.`
      );
    }
  }, [deviceCandidate, system]);

  useEffect(() => {
    if ((thresholds.precision_mode === "exact_pixel" || lightweight) && thresholds.clip_enabled) {
      setThresholds((current) => ({ ...current, clip_enabled: false }));
    }
  }, [lightweight, thresholds.clip_enabled, thresholds.precision_mode]);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (startDisabledReason !== null) return;
    onSubmit({
      path: path.trim(),
      recursive,
      device,
      gpu_index: usesCudaIndex ? gpuIndex : null,
      model: model.trim() || undefined,
      pretrained: pretrained.trim() || undefined,
      allow_clip_fallback: allowClipFallback,
      thresholds
    });
  }

  function changePrecisionMode(mode: PrecisionMode) {
    setThresholds((current) => ({
      ...current,
      ...thresholdPresets[mode],
      ...(mode === "exact_pixel" || current.performance_profile === "lightweight"
        ? { clip_enabled: false }
        : {}),
      precision_mode: mode
    }));
  }

  function changePerformanceProfile(profile: PerformanceProfile) {
    setThresholds((current) => ({
      ...current,
      ...(profile === "lightweight" ? { clip_enabled: false } : {}),
      performance_profile: profile
    }));
  }

  function changeThreshold<K extends keyof NumericThresholds>(key: K, value: number) {
    setThresholds((current) => ({ ...current, [key]: value }));
  }

  async function browseFolder() {
    setPickerBusy(true);
    setPickerError(null);
    try {
      const result = await chooseFolder(path);
      if (result.path) {
        setPath(result.path);
      }
    } catch (error: unknown) {
      setPickerError(error instanceof Error ? error.message : String(error));
    } finally {
      setPickerBusy(false);
    }
  }

  return (
    <section className="panel scan-panel">
      <div className="panel-header">
        <div>
          <h2 ref={headingRef} tabIndex={-1}>
            Scan
          </h2>
        </div>
        <FolderSearch size={22} />
      </div>
      <form onSubmit={submit} className="scan-form">
        {template ? (
          <p className="scan-template-notice" role="status">
            Settings from scan #{template.sourceScanId} loaded. {deviceFallbackNotice ? `${deviceFallbackNotice} ` : ""}
            {reusedDeviceNeedsValidation && !system
              ? "Device availability will be checked when system status loads. "
              : ""}
          </p>
        ) : null}
        <div className="folder-picker">
          <label className="field wide">
            <span>Image or folder path</span>
            <input
              value={path}
              placeholder="画像またはフォルダーのパスを入力"
              autoComplete="off"
              spellCheck={false}
              aria-describedby={pickerError ? "folder-picker-error" : undefined}
              onChange={(event) => setPath(event.target.value)}
            />
          </label>
          <button
            type="button"
            className="secondary-button"
            disabled={busy || pickerBusy}
            onClick={browseFolder}
          >
            <FolderOpen size={18} />
            <span>{pickerBusy ? "Opening" : "Choose folder"}</span>
          </button>
        </div>
        {pickerError ? (
          <p className="field-note" id="folder-picker-error" role="alert">
            {pickerError}
          </p>
        ) : null}

        <div className="basic-settings">
          <label className="toggle">
            <input
              type="checkbox"
              checked={recursive}
              onChange={(event) => setRecursive(event.target.checked)}
            />
            <span>Include subfolders</span>
          </label>
          <div className="basic-settings-grid">
            <label className="field precision-field" title={precisionDescription(thresholds.precision_mode)}>
              <span>Precision mode</span>
              <select
                aria-label="Precision mode"
                value={thresholds.precision_mode}
                onChange={(event) => changePrecisionMode(event.target.value as PrecisionMode)}
              >
                {precisionOptions.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="field precision-field" title={performanceDescription(thresholds.performance_profile)}>
              <span>Performance profile</span>
              <select
                aria-label="Performance profile"
                value={thresholds.performance_profile}
                onChange={(event) =>
                  changePerformanceProfile(event.target.value as PerformanceProfile)
                }
              >
                {performanceOptions.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </div>

        <button
          type="button"
          className="advanced-settings-toggle"
          aria-expanded={advancedOpen}
          aria-controls={advancedSettingsId}
          onClick={() => setAdvancedOpen((current) => !current)}
        >
          <SlidersHorizontal size={18} />
          <span>Advanced settings</span>
          <ChevronDown size={18} className={advancedOpen ? "disclosure-open" : ""} />
        </button>

        {advancedOpen ? (
          <div className="advanced-settings" id={advancedSettingsId}>
            <div className="form-row">
              <label
                className="toggle"
                title={
                  thresholds.precision_mode === "exact_pixel"
                    ? "Exact pixel mode does not use CLIP"
                    : lightweight
                      ? "Lightweight fast mode does not use CLIP"
                      : clipAvailable
                        ? ""
                        : system?.clip.error ?? "CLIP is unavailable"
                }
              >
                <input
                  type="checkbox"
                  checked={thresholds.clip_enabled}
                  disabled={!clipAvailable || thresholds.precision_mode === "exact_pixel" || lightweight}
                  onChange={(event) =>
                    setThresholds((current) => ({ ...current, clip_enabled: event.target.checked }))
                  }
                />
                <span>CLIP check</span>
              </label>
              <label className="toggle">
                <input
                  type="checkbox"
                  checked={allowClipFallback}
                  onChange={(event) => setAllowClipFallback(event.target.checked)}
                />
                <span>Fallback if CLIP fails</span>
              </label>
            </div>
            {!clipAvailable ? (
              <p className="field-note">CLIP is unavailable. Visual hash matching remains available.</p>
            ) : null}
            <div className="form-grid">
              <label className="field">
                <span>Device</span>
                <select
                  value={device}
                  onChange={(event) => {
                    setDevice(event.target.value);
                    setDeviceFallbackNotice(null);
                  }}
                >
                  {devices.map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>GPU index</span>
                <input
                  type="number"
                  min={0}
                  value={gpuIndex ?? ""}
                  disabled={!usesCudaIndex}
                  onChange={(event) => {
                    setGpuIndex(event.target.value === "" ? null : Number(event.target.value));
                    setDeviceFallbackNotice(null);
                  }}
                />
              </label>
              <label className="field">
                <span>Model</span>
                <input
                  value={model}
                  onChange={(event) => setModel(event.target.value)}
                  placeholder={system?.default_model ?? "ViT-B-32"}
                />
              </label>
              <label className="field">
                <span>Pretrained</span>
                <input
                  value={pretrained}
                  onChange={(event) => setPretrained(event.target.value)}
                  placeholder={system?.default_pretrained ?? "laion2b_s34b_b79k"}
                />
              </label>
            </div>
            <div className="thresholds">
              <div className="threshold-title">
                <SlidersHorizontal size={18} />
                <span>Custom thresholds</span>
              </div>
              <RangeField
                label="pHash distance"
                min={0}
                max={64}
                step={1}
                value={thresholds.phash_distance}
                onChange={(value) => changeThreshold("phash_distance", value)}
              />
              <RangeField
                label="Histogram"
                min={0}
                max={1}
                step={0.01}
                value={thresholds.histogram_similarity}
                onChange={(value) => changeThreshold("histogram_similarity", value)}
              />
              <RangeField
                label="CLIP cosine"
                min={0}
                max={1}
                step={0.01}
                value={thresholds.clip_similarity}
                onChange={(value) => changeThreshold("clip_similarity", value)}
              />
            </div>
          </div>
        ) : null}

        {startDisabledReason && path.trim() ? (
          <p className="start-disabled-reason">
            {startDisabledReason}
          </p>
        ) : null}

        <button
          className="primary-button"
          title={startDisabledReason ?? undefined}
          disabled={startDisabledReason !== null}
        >
          <Play size={18} />
          <span>{busy ? "Submitting" : "Start scan"}</span>
        </button>
      </form>
    </section>
  );
}

function RangeField({
  label,
  min,
  max,
  step,
  value,
  onChange
}: {
  label: string;
  min: number;
  max: number;
  step: number;
  value: number;
  onChange: (value: number) => void;
}) {
  const rangeId = useId();
  const numberId = useId();
  const displayValue = formatThresholdValue(value, step);

  function updateValue(next: number) {
    if (!Number.isFinite(next)) return;
    onChange(normalizeThresholdValue(next, min, max, step));
  }

  return (
    <div className="range-field">
      <label htmlFor={rangeId}>{label}</label>
      <input
        id={rangeId}
        aria-label={`${label} slider`}
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => updateValue(Number(event.target.value))}
      />
      <input
        id={numberId}
        className="threshold-number"
        aria-label={`${label} value`}
        type="number"
        min={min}
        max={max}
        step={step}
        value={displayValue}
        onChange={(event) => updateValue(Number(event.target.value))}
      />
    </div>
  );
}

function normalizeThresholdValue(value: number, min: number, max: number, step: number): number {
  const clamped = Math.min(max, Math.max(min, value));
  if (step >= 1) return Math.round(clamped);
  return Number(clamped.toFixed(decimalPlaces(step)));
}

function formatThresholdValue(value: number, step: number): string {
  if (step >= 1) return String(Math.round(value));
  return value.toFixed(decimalPlaces(step));
}

function decimalPlaces(value: number): number {
  const [, fraction = ""] = String(value).split(".");
  return fraction.length;
}

function precisionDescription(mode: PrecisionMode): string {
  switch (mode) {
    case "exact_pixel":
      return "Only identical decoded pixels. Fastest to review, with the narrowest detection range.";
    case "ultra_strict":
      return "Near-identical images only. Fast review, but lightly edited copies may be missed.";
    case "balanced":
      return "Finds more resized or recompressed copies and needs closer review.";
    case "broad":
      return "Widest search. Expect more manual review before moving files.";
    default:
      return "Narrow visual search with moderate speed. Manually review matches before moving files.";
  }
}

function performanceDescription(profile: PerformanceProfile): string {
  if (profile === "lightweight") {
    return "Faster and uses less CPU and memory, but may miss resized or lightly edited copies.";
  }
  return "Uses the full evidence set for broader detection, with higher CPU and memory use.";
}
