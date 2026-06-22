import { useEffect, useState } from "react";
import { FileText } from "lucide-react";
import { apiGet, openArtifact } from "../api";
import { useZones } from "../zonesContext";
import { Panel, Dot, Badge } from "../ui";
import type {
  ControlStatus,
  PipelineLatencyEvidence,
  PipelineZone,
  Stage,
  UniverseSweepTracking,
  VectorbtPaidScreenTracking,
} from "../types";

function meta(s: Stage): [string, unknown][] {
  const keys = [
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
    "screening_status",
    "screening_artifact",
    "screening_artifact_hash",
    "robustness_artifact",
    "surface_stability_status",
    "surface_formula_authority_status",
    "replay_eligibility_status",
    "replay_status",
    "replay_detail",
    "replay_artifact",
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

function isRepoRelativePath(v: unknown): v is string {
  if (typeof v !== "string") return false;
  const text = v.trim();
  if (!text || text.startsWith("/") || text.startsWith("\\") || /^[a-z]+:\/\//i.test(text) || /^[A-Za-z]:[\\/]/.test(text)) return false;
  return text.includes("/") || text.includes("\\") || /\.[A-Za-z0-9]+$/.test(text);
}

function artifactLabel(label: string): string {
  return label.replace(/_(artifact|doc)$/u, "").replace(/_/g, " ");
}

function ArtifactAction({ label, value }: { label: string; value: string }) {
  return (
    <button type="button" onClick={() => void openArtifact(value)}
      className="chip max-w-full hover:border-accent/60 hover:text-accent" title={value}>
      <FileText size={12} />
      <span className="truncate">{artifactLabel(label)}</span>
    </button>
  );
}

function shouldLinkMeta(key: string, value: unknown): value is string {
  return key.includes("artifact") && isRepoRelativePath(value);
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
  return "dim";
}

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function fmtEta(seconds: unknown): string {
  const value = num(seconds);
  if (value == null) return "--";
  if (value < 3600) return `${Math.ceil(value / 60)}m`;
  return `${(value / 3600).toFixed(1)}h`;
}

function fmtCount(value: number | null): string {
  return value == null ? "--" : String(value);
}

function VectorbtPaidScreenBand({ tracking }: { tracking?: VectorbtPaidScreenTracking }) {
  const state = (tracking?.state ?? "idle").trim().toLowerCase() || "idle";
  const expected = num(tracking?.expected_work_units);
  const completed = num(tracking?.completed_work_units);
  const failed = num(tracking?.failed_work_units);
  const skipped = num(tracking?.skipped_work_units);
  const hasKnownCount = completed != null || failed != null || skipped != null;
  const accounted = hasKnownCount ? (completed ?? 0) + (failed ?? 0) + (skipped ?? 0) : null;
  const pct = accounted != null && expected != null && expected > 0 ? Math.min(100, (accounted / expected) * 100) : null;
  const hasAnomalies = (tracking?.anomalies?.length ?? 0) > 0;
  const hasRejectedUnits = (failed != null && failed > 0) || (skipped != null && skipped > 0);
  const allCountsKnown = expected != null && completed != null && failed != null && skipped != null;
  const cleanComplete = (
    expected != null &&
    expected > 0 &&
    allCountsKnown &&
    failed === 0 &&
    skipped === 0 &&
    accounted === expected &&
    !hasAnomalies
  );
  const displayState = hasRejectedUnits ? "partial_failed" : state === "complete" && !cleanComplete ? "stalled" : state;
  const badgeTone = displayState === "running" ? "accent" : displayState === "complete" ? "ok" : "warn";
  const artifacts: [string, string | null | undefined][] = [
    ["status", tracking?.status_artifact],
    ["log", tracking?.log_artifact],
    ["manifest", tracking?.manifest_artifact],
    ["artifact", tracking?.artifact],
    ["ready_gate", tracking?.ready_gate_artifact],
    ["declaration", tracking?.declaration_artifact],
    ["units_jsonl", tracking?.units_jsonl_artifact],
  ];
  return (
    <div className="rounded-lg border border-line bg-bg-elev/40 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="text-sm font-semibold">VectorBT paid screen (Vast)</div>
        <Badge tone={badgeTone}>{displayState}</Badge>
        <Badge tone={tracking?.host_kind === "rented" || tracking?.ssh_host ? "ok" : "warn"}>{tracking?.host_label ?? tracking?.ssh_host ?? "unknown"}</Badge>
        {tracking?.workers != null && <Badge tone="dim">workers {tracking.workers}</Badge>}
      </div>
      {accounted != null && (
        <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-bg">
          <div className="h-full bg-accent" style={{ width: `${pct ?? 0}%` }} />
        </div>
      )}
      <div className="mt-2 grid gap-1 mono text-[11px] text-ink-faint sm:grid-cols-2 lg:grid-cols-4">
        <div className="truncate" title={tracking?.run_id ?? undefined}>run: <span className="text-ink">{tracking?.run_id ?? "--"}</span></div>
        {accounted != null && <div>progress: <span className="text-ink">{accounted}{expected ? `/${expected}` : ""}{pct != null ? ` (${pct.toFixed(1)}%)` : ""}</span></div>}
        <div>failed/skipped: <span className="text-ink">{fmtCount(failed)}/{fmtCount(skipped)}</span></div>
        <div>batches: <span className="text-ink">{tracking?.collected_batches ?? "--"}{tracking?.expected_batches != null ? `/${tracking.expected_batches}` : ""}</span></div>
        <div>rate: <span className="text-ink">{tracking?.units_per_hour != null ? `${tracking.units_per_hour.toFixed(1)}/h` : "--"}</span></div>
        <div>eta: <span className="text-ink">{tracking?.eta_utc ?? fmtEta(tracking?.eta_seconds)}</span></div>
        <div>sync: <span className="text-ink">{tracking?.last_sync_utc ? displayValue(tracking.last_sync_utc) : "--"}</span></div>
        <div>tmux: <span className="text-ink">{tracking?.tmux_session ?? "--"}</span></div>
      </div>
      {artifacts.some(([, value]) => isRepoRelativePath(value)) && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <span className="text-[11px] uppercase tracking-wide text-ink-faint">artifacts</span>
          {artifacts.map(([label, value]) => isRepoRelativePath(value) ? <ArtifactAction key={`${label}:${value}`} label={label} value={value} /> : null)}
        </div>
      )}
      {tracking?.anomalies && tracking.anomalies.length > 0 && (
        <div className="mt-2 max-w-full break-words mono text-[11px] text-warn">anomalies: {tracking.anomalies.join("; ")}</div>
      )}
      {tracking?.detail && <div className="mt-2 mono text-[11px] text-warn">{tracking.detail}</div>}
    </div>
  );
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
        <Badge tone={ackStatus === "MEASURED" ? "ok" : "warn"}>defensive ack {ackStatus}</Badge>
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
        <VectorbtPaidScreenBand tracking={p.vectorbt_paid_screen_tracking} />
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
              {meta(s).map(([k, v]) => (
                <div key={k} className="flex min-w-0 items-center gap-1">
                  <span>{k}:</span>
                  {shouldLinkMeta(k, v) ? <ArtifactAction label={k} value={v} /> : <span>{displayValue(v)}</span>}
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </Panel>
  );
}
