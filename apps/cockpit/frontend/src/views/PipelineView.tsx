import { useEffect, useMemo, useState } from "react";
import { Play, RefreshCw } from "lucide-react";
import { apiGet, apiPost } from "../api";
import { useZones } from "../zonesContext";
import { Panel, Dot, Badge } from "../ui";
import type { ControlJobLog, ControlStatus, PipelineLatencyEvidence, PipelineZone, Stage } from "../types";

const SWEEP_JOB = "cme_m6_universe_sweep";

function meta(s: Stage): [string, unknown][] {
  const keys = [
    "detail",
    "q001_status",
    "missing_or_unavailable_slots",
    "data_doctor_status",
    "strict_mbo_gap_count",
    "strict_mbo_stale_gap_count",
    "gap_count",
    "artifact",
    "scope",
    "evaluated_model_rows",
    "evaluated_model_count",
    "robustness_status",
    "robustness_detail",
    "pbo",
    "units_run",
    "units_skipped",
    "units_errored",
    "survivors",
    "row_count",
    "known_gaps",
    "candidates",
    "run_end",
    "captured_at",
  ];
  return keys.map((k) => [k, s[k]]).filter(([, v]) => v !== undefined && v !== null && v !== "") as [string, unknown][];
}

function displayValue(v: unknown): string {
  const text = Array.isArray(v)
    ? v.join(", ")
    : typeof v === "object" && v !== null
      ? JSON.stringify(v)
      : String(v).replace("T", " ");
  return text.length > 80 ? `${text.slice(0, 77)}...` : text;
}

function fmtUs(v: unknown): string {
  return typeof v === "number" && Number.isFinite(v) ? `${v.toFixed(3)} us` : "--";
}

function fmtMs(v: unknown): string {
  return typeof v === "number" && Number.isFinite(v) ? `${v.toFixed(6)} ms` : "--";
}

function ControlBand({
  control,
  controlError,
  launching,
  latestSweep,
  log,
  logError,
  onLaunch,
  onRefresh,
}: {
  control: ControlStatus | null;
  controlError: string | null;
  launching: boolean;
  latestSweep?: ControlStatus["tracked_jobs"][number];
  log: ControlJobLog | null;
  logError: string | null;
  onLaunch: () => void;
  onRefresh: () => void;
}) {
  const state = latestSweep?.state ?? "idle";
  const active = state === "pending" || state === "running";
  const execOn = control?.exec_enabled === true;
  const disabled = !execOn || active || launching;
  return (
    <div className="grid gap-3 lg:grid-cols-[1.1fr_1fr]">
      <div className="rounded-lg border border-line bg-bg-elev/40 p-3">
        <div className="flex flex-wrap items-center gap-2">
          <div className="text-sm font-semibold">CME M6 sweep</div>
          <Badge tone={execOn ? "ok" : "warn"}>exec {execOn ? "on" : "off"}</Badge>
          <Badge tone={active ? "accent" : "dim"}>{state}</Badge>
          <button
            className="ml-auto inline-flex h-8 items-center gap-2 rounded-md border border-accent/50 px-3 text-xs font-semibold text-accent disabled:border-line disabled:text-ink-faint"
            disabled={disabled}
            onClick={onLaunch}
            title={execOn ? "Launch queued full CME M6 sweep" : "COCKPIT_CONTROL_EXEC is off"}
          >
            <Play size={14} /> Launch
          </button>
          <button
            className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-line text-ink-dim hover:text-ink"
            onClick={onRefresh}
            title="Refresh control status"
          >
            <RefreshCw size={14} />
          </button>
        </div>
        <div className="mono mt-2 space-y-0.5 break-words text-[11px] text-ink-faint">
          <div>job: {SWEEP_JOB}</div>
          <div>latest_id: {latestSweep?.job_id ?? "--"}</div>
          <div>host: {latestSweep?.host ?? "laptop"}</div>
          {controlError && <div className="text-bad">control_error: {controlError}</div>}
        </div>
      </div>
      <div className="rounded-lg border border-line bg-bg-elev/40 p-3">
        <div className="mb-2 flex items-center gap-2 text-sm font-semibold">
          <Dot status={log?.state ?? latestSweep?.state ?? "unknown"} /> Sweep log tail
        </div>
        <pre className="max-h-32 overflow-auto whitespace-pre-wrap rounded-md bg-bg px-2 py-1 text-[11px] leading-5 text-ink-dim">
          {logError || log?.log_tail || "no sweep log selected"}
        </pre>
      </div>
    </div>
  );
}

function LatencyBand({ evidence }: { evidence?: PipelineLatencyEvidence }) {
  const ackStatus = evidence?.defensive_cancel_ack_status ?? "UNKNOWN";
  return (
    <div className="rounded-lg border border-line bg-bg-elev/40 p-3">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="text-sm font-semibold">Latency evidence</div>
        <Badge tone={evidence?.status === "ok" ? "ok" : "warn"}>{evidence?.status ?? "unknown"}</Badge>
        <Badge tone={ackStatus === "UNMEASURED" ? "warn" : "ok"}>defensive ack {ackStatus}</Badge>
      </div>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <div className="mono text-[11px] text-ink-faint">ack_p99: <span className="text-ink">{fmtUs(evidence?.ack_p99_us)}</span></div>
        <div className="mono text-[11px] text-ink-faint">m6_band: <span className="text-ink">{fmtMs(evidence?.m6_band_ms)}</span></div>
        <div className="mono text-[11px] text-ink-faint">engine: <span className="text-ink">{fmtUs(evidence?.offensive_engine_us)}</span></div>
        <div className="mono text-[11px] text-ink-faint">baseline_send: <span className="text-ink">{fmtUs(evidence?.offensive_baseline_tick_to_send_us)}</span></div>
        <div className="mono text-[11px] text-ink-faint">decision_p99: <span className="text-ink">{fmtUs(evidence?.offensive_latest_decision_to_send_p99_us)}</span></div>
        <div className="mono text-[11px] text-ink-faint">cancel_send: <span className="text-ink">{fmtUs(evidence?.defensive_cancel_to_send_us)}</span></div>
        <div className="mono text-[11px] text-ink-faint">live_ready: <span className="text-ink">{evidence?.live_readiness_status ?? "unknown"}</span></div>
        <div className="mono text-[11px] text-ink-faint">band_source: <span className="text-ink">paper ack p99</span></div>
      </div>
    </div>
  );
}

export function PipelineView() {
  const p = useZones().pipeline as PipelineZone | undefined;
  const [control, setControl] = useState<ControlStatus | null>(null);
  const [controlError, setControlError] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [log, setLog] = useState<ControlJobLog | null>(null);
  const [logError, setLogError] = useState<string | null>(null);
  const [launching, setLaunching] = useState(false);

  const latestSweep = useMemo(() => {
    const tracked = control?.tracked_jobs ?? [];
    return [...tracked].reverse().find((j) => j.name === SWEEP_JOB);
  }, [control]);
  const selectedJobId = jobId ?? latestSweep?.job_id ?? null;

  async function refreshControl() {
    try {
      const status = await apiGet<ControlStatus>("/api/control/status");
      setControl(status);
      setControlError(null);
    } catch (err) {
      setControlError(err instanceof Error ? err.message : String(err));
    }
  }

  useEffect(() => {
    refreshControl();
    const id = window.setInterval(refreshControl, 5000);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    const activeJobId = selectedJobId;
    if (!activeJobId) return;
    const encodedJobId = encodeURIComponent(activeJobId);
    let closed = false;
    async function refreshLog() {
      try {
        const payload = await apiGet<ControlJobLog>(`/api/control/job/${encodedJobId}/logs?tail=120`);
        if (!closed) {
          setLog(payload);
          setLogError(null);
        }
      } catch (err) {
        if (!closed) setLogError(err instanceof Error ? err.message : String(err));
      }
    }
    refreshLog();
    const id = window.setInterval(refreshLog, 3000);
    return () => {
      closed = true;
      window.clearInterval(id);
    };
  }, [selectedJobId]);

  async function launchSweep() {
    setLaunching(true);
    try {
      const result = await apiPost<{ job_id: string }>("/api/control/job", { name: SWEEP_JOB, confirm: true });
      setJobId(result.job_id);
      setControlError(null);
      await refreshControl();
    } catch (err) {
      setControlError(err instanceof Error ? err.message : String(err));
    } finally {
      setLaunching(false);
    }
  }

  if (!p) return <Panel title="Pipeline"><span className="text-ink-dim">loading…</span></Panel>;
  return (
    <Panel title="Pipeline — start → end" right={<span className="capitalize">{p.health}</span>}>
      <div className="mb-3 grid gap-3">
        <ControlBand
          control={control}
          controlError={controlError}
          launching={launching}
          latestSweep={latestSweep}
          log={log}
          logError={logError}
          onLaunch={launchSweep}
          onRefresh={refreshControl}
        />
        <LatencyBand evidence={p.latency_evidence} />
      </div>
      <div className="grid gap-3 md:grid-cols-3 lg:grid-cols-6">
        {p.stages.map((s) => (
          <div key={s.id} className="rounded-lg border border-line bg-bg-elev/50 p-3">
            <div className="flex items-center gap-2 text-sm font-semibold"><Dot status={s.status} /> {s.label}</div>
            <div className="mono mt-2 space-y-0.5 break-words text-[11px] text-ink-faint">
              {meta(s).slice(0, 8).map(([k, v]) => (
                <div key={k}>{k}: {displayValue(v)}</div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </Panel>
  );
}
