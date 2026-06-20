import { useEffect, useState } from "react";
import { apiGet } from "../api";
import { useZones } from "../zonesContext";
import { Panel, Dot, Badge } from "../ui";
import type {
  ControlStatus,
  PipelineLatencyEvidence,
  PipelineZone,
  Stage,
  UniverseSweepTracking,
} from "../types";

function meta(s: Stage): [string, unknown][] {
  const keys = [
    "screening_status",
    "screening_artifact",
    "screening_artifact_hash",
    "replay_status",
    "replay_detail",
    "replay_eligibility_status",
    "replay_artifact",
    "robustness_artifact",
    "surface_stability_status",
    "surface_formula_authority_status",
    "detail",
    "skip_reason_counts",
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

function bandTone(status?: string): "ok" | "warn" | "dim" {
  if (status === "MEASURED") return "ok";
  if (status === "OPEN") return "warn";
  return "warn";
}

function SweepTrackingBand({ tracking }: { tracking?: UniverseSweepTracking }) {
  const state = tracking?.state ?? "idle";
  const hostKind = tracking?.host_kind ?? "unknown";
  const progress = tracking?.progress ?? {};
  const remaining = typeof progress.remaining === "number" ? progress.remaining : null;
  const total = typeof progress.total === "number" ? progress.total : null;
  const currentUnit = typeof progress.current_unit === "number" ? progress.current_unit : null;
  return (
    <div className="grid gap-3 lg:grid-cols-[1.1fr_1fr]">
      <div className="rounded-lg border border-line bg-bg-elev/40 p-3">
        <div className="flex flex-wrap items-center gap-2">
          <div className="text-sm font-semibold">M6 universe sweep (read-only)</div>
          <Badge tone={state === "running" ? "accent" : state === "complete" ? "ok" : "warn"}>{state}</Badge>
          <Badge tone={hostKind === "rented" || hostKind === "external" ? "ok" : "warn"}>{tracking?.host_label ?? hostKind}</Badge>
          {tracking?.workers != null && <Badge tone="dim">workers {tracking.workers}</Badge>}
        </div>
        <div className="mono mt-2 space-y-0.5 break-words text-[11px] text-ink-faint">
          <div>mode: {tracking?.tracking_mode ?? "read_only_external"}</div>
          <div>log: {tracking?.log_artifact ?? "--"}</div>
          <div>checkpoint: {tracking?.checkpoint_artifact ?? "--"}</div>
          <div>output: {tracking?.output_artifact ?? "--"}</div>
          <div>commit: {tracking?.git_commit ?? "--"}</div>
          {total != null && (
            <div>
              work_units: total={total}
              {remaining != null ? ` remaining=${remaining}` : ""}
              {currentUnit != null ? ` last_unit=${currentUnit}` : ""}
            </div>
          )}
          {tracking?.detail && <div className="text-warn">{tracking.detail}</div>}
          <div>repo map: {tracking?.repo_state_doc ?? "docs/REPO_STATE.md"}</div>
          <div>monitor: {tracking?.monitor_doc ?? "runtime/monitor/universe_M6_full_watch.md"}</div>
          <div className="text-ink-dim">Local cockpit launch retired; track Vast/rented runs via log + checkpoint artifacts.</div>
        </div>
      </div>
      <div className="rounded-lg border border-line bg-bg-elev/40 p-3">
        <div className="mb-2 flex items-center gap-2 text-sm font-semibold">
          <Dot status={state} /> Sweep log tail
        </div>
        <pre className="max-h-32 overflow-auto whitespace-pre-wrap rounded-md bg-bg px-2 py-1 text-[11px] leading-5 text-ink-dim">
          {tracking?.log_tail || "no universe_M6 log detected under runtime/"}
        </pre>
      </div>
    </div>
  );
}

function LatencyBand({ evidence }: { evidence?: PipelineLatencyEvidence }) {
  const ackStatus = evidence?.defensive_cancel_ack_status ?? "UNKNOWN";
  const bands = evidence?.component_bands ?? [];
  const livePlacement = evidence?.live_placement as Record<string, unknown> | undefined;
  const realism = evidence?.execution_realism as Record<string, unknown> | undefined;
  return (
    <div className="rounded-lg border border-line bg-bg-elev/40 p-3">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="text-sm font-semibold">Latency evidence</div>
        <Badge tone={evidence?.status === "ok" ? "ok" : "warn"}>{evidence?.status ?? "unknown"}</Badge>
        <Badge tone={ackStatus === "UNMEASURED" ? "warn" : "ok"}>defensive ack {ackStatus}</Badge>
        <Badge tone={evidence?.hftbacktest_critical_bands_measured ? "ok" : "warn"}>
          hbt critical bands {evidence?.hftbacktest_critical_bands_measured ? "measured" : "open"}
        </Badge>
      </div>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <div className="mono text-[11px] text-ink-faint">ack_p99: <span className="text-ink">{fmtUs(evidence?.ack_p99_us)}</span></div>
        <div className="mono text-[11px] text-ink-faint">m6_band: <span className="text-ink">{fmtMs(evidence?.m6_band_ms)}</span></div>
        <div className="mono text-[11px] text-ink-faint">engine: <span className="text-ink">{fmtUs(evidence?.offensive_engine_us)}</span></div>
        <div className="mono text-[11px] text-ink-faint">baseline_send: <span className="text-ink">{fmtUs(evidence?.offensive_baseline_tick_to_send_us)}</span></div>
        <div className="mono text-[11px] text-ink-faint">decision_p99: <span className="text-ink">{fmtUs(evidence?.offensive_latest_decision_to_send_p99_us)}</span></div>
        <div className="mono text-[11px] text-ink-faint">cancel_send: <span className="text-ink">{fmtUs(evidence?.defensive_cancel_to_send_us)}</span></div>
        <div className="mono text-[11px] text-ink-faint">live_ready: <span className="text-ink">{evidence?.live_readiness_status ?? "unknown"}</span></div>
        <div className="mono text-[11px] text-ink-faint">source: <span className="text-ink">runtime/latency_reports/latency_truth.json</span></div>
      </div>
      {livePlacement && (
        <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          <div className="mono text-[11px] text-ink-faint">live host: <span className="text-ink">{String(livePlacement.host ?? "--")}</span></div>
          <div className="mono text-[11px] text-ink-faint">live run: <span className="text-ink">{String(livePlacement.run_id ?? "--")}</span></div>
          <div className="mono text-[11px] text-ink-faint">new ack pairs: <span className="text-ink">{String(livePlacement.paired_new_ack ?? "--")}</span></div>
          <div className="mono text-[11px] text-ink-faint">cancel ack pairs: <span className="text-ink">{String(livePlacement.cancel_ack ?? "--")}</span></div>
        </div>
      )}
      {realism && (
        <div className="mt-2 mono text-[11px] text-ink-faint">
          regimes: {realism.hftbacktest_regimes_present ? String(realism.hftbacktest_regime_count ?? 0) : "missing"}
          {" · "}
          cc ingest: {realism.cc_component_ingest_present ? String(realism.cc_component_ingest_utc ?? "present") : "missing"}
        </div>
      )}
      {bands.length > 0 && (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-[11px]">
            <thead className="text-ink-faint">
              <tr>
                <th className="pb-1 pr-2 font-medium">component</th>
                <th className="pb-1 pr-2 font-medium">status</th>
                <th className="pb-1 pr-2 font-medium">p99</th>
                <th className="pb-1 font-medium">note</th>
              </tr>
            </thead>
            <tbody>
              {bands.map((band) => (
                <tr key={band.name} className="border-t border-line/60 align-top">
                  <td className="mono py-1 pr-2 text-ink">{band.name}</td>
                  <td className="py-1 pr-2"><Badge tone={bandTone(band.measurement_status)}>{band.measurement_status}</Badge></td>
                  <td className="mono py-1 pr-2 text-ink">{fmtUs(band.p99_us)}</td>
                  <td className="py-1 text-ink-dim">{band.note ? displayValue(band.note) : "--"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export function PipelineView() {
  const p = useZones().pipeline as PipelineZone | undefined;
  const [controlError, setControlError] = useState<string | null>(null);

  async function refreshControl() {
    try {
      await apiGet<ControlStatus>("/api/control/status");
      setControlError(null);
    } catch (err) {
      setControlError(err instanceof Error ? err.message : String(err));
    }
  }

  useEffect(() => {
    refreshControl();
    const id = window.setInterval(refreshControl, 15000);
    return () => window.clearInterval(id);
  }, []);

  if (!p) return <Panel title="Pipeline"><span className="text-ink-dim">loading…</span></Panel>;
  return (
    <Panel title="Pipeline — start → end" right={<span className="capitalize">{p.health}</span>}>
      <div className="mb-3 grid gap-3">
        <SweepTrackingBand tracking={p.universe_sweep_tracking} />
        {controlError && (
          <div className="mono rounded-lg border border-line bg-bg-elev/40 p-2 text-[11px] text-bad">
            control_status_error: {controlError}
          </div>
        )}
        <LatencyBand evidence={p.latency_evidence} />
      </div>
      <div className="grid gap-3 md:grid-cols-3 lg:grid-cols-6">
        {p.stages.map((s) => (
          <div key={s.id} className="rounded-lg border border-line bg-bg-elev/50 p-3">
            <div className="flex items-center gap-2 text-sm font-semibold"><Dot status={s.status} /> {s.label}</div>
            <div className="mono mt-2 space-y-0.5 break-words text-[11px] text-ink-faint">
              {meta(s).slice(0, 12).map(([k, v]) => (
                <div key={k}>{k}: {displayValue(v)}</div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </Panel>
  );
}
