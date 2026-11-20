import { Cpu, Database, KeyRound, MonitorCog, RefreshCw, RotateCcw, ScanSearch } from "lucide-react";
import { useEffect, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import type { SystemResponse } from "../types/api";

type Props = {
  system: SystemResponse | null;
  loading: boolean;
  apiToken: string;
  resetBusy: boolean;
  onApiTokenChange: (value: string) => void;
  onRefresh: () => void;
  onReset: () => void;
};

export function SystemStatus({
  system,
  loading,
  apiToken,
  resetBusy,
  onApiTokenChange,
  onRefresh,
  onReset
}: Props) {
  const [tokenDraft, setTokenDraft] = useState(apiToken);

  useEffect(() => setTokenDraft(apiToken), [apiToken]);

  function submitToken(event: FormEvent) {
    event.preventDefault();
    if (loading || resetBusy) return;
    onApiTokenChange(tokenDraft);
  }

  return (
    <section className="panel system-panel">
      <div className="panel-header">
        <div>
          <h2 title={system?.platform}>System</h2>
        </div>
        <button
          className="icon-button"
          onClick={onRefresh}
          disabled={loading || resetBusy}
          title="Refresh system status"
          aria-label="Refresh system status"
        >
          <RefreshCw size={18} className={loading ? "spin" : ""} />
        </button>
      </div>

      <form className="token-form" onSubmit={submitToken}>
        <KeyRound size={16} />
        <input
          type="password"
          value={tokenDraft}
          onChange={(event) => setTokenDraft(event.target.value)}
          placeholder="Local API token"
          aria-label="Local API token"
          autoComplete="off"
          disabled={loading || resetBusy}
        />
        <button className="secondary-button" disabled={loading || resetBusy}>
          Apply
        </button>
      </form>

      <div className="status-grid">
        <StatusItem
          icon={<Cpu size={18} />}
          label="Device"
          value={system?.device.selected ?? "-"}
          detail={system?.device.cuda_devices.join(", ") || (system?.device.mps_available ? "MPS" : "CPU")}
        />
        <StatusItem
          icon={<MonitorCog size={18} />}
          label="Torch"
          value={system?.device.torch_available ? system.device.torch_version ?? "available" : "not installed"}
          detail={`Python ${system?.python ?? "-"}`}
        />
        <StatusItem
          icon={<ScanSearch size={18} />}
          label="CLIP"
          value={system?.clip.available ? "available" : "not available"}
          detail={system?.clip.version ?? system?.clip.error ?? "-"}
        />
        <StatusItem
          icon={<Database size={18} />}
          label="Storage"
          value="SQLite"
          detail={system?.db_path ?? "-"}
        />
      </div>
      <div className="system-actions">
        <button
          className="danger-button"
          type="button"
          onClick={onReset}
          disabled={loading || resetBusy}
        >
          <RotateCcw size={16} />
          Reset history & cache
        </button>
      </div>
    </section>
  );
}

function StatusItem({
  icon,
  label,
  value,
  detail
}: {
  icon: ReactNode;
  label: string;
  value: string;
  detail: string;
}) {
  return (
    <div className="status-item">
      <div className="status-icon">{icon}</div>
      <div>
        <span>{label}</span>
        <strong title={detail}>{value}</strong>
      </div>
    </div>
  );
}
