import { Fragment, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { apiPost } from "../api";
import { useZones } from "../zonesContext";
import { Badge, Dot, Kpi, Panel } from "../ui";
import type { LifecycleRow, LifecycleZone } from "../types";
import { lifecycleLiveKpiTone, lifecycleSubmitLabel, lifecycleSubmitTone } from "../lifecycleUi";

const LANES = [
  "CANDIDATE", "SCREENING", "GAUNTLET", "CERTIFIED", "SHADOW", "LIVE",
  "DEGRADED", "ARCHIVED_PAUSED", "QUARANTINED", "RETIRED",
];

function fmtAge(ts: string | null | undefined): string {
  if (!ts) return "—";
  const ms = Date.now() - Date.parse(ts);
  if (Number.isNaN(ms)) return ts.slice(0, 19);
  const h = Math.floor(ms / 3_600_000);
  if (h < 1) return `${Math.max(1, Math.floor(ms / 60_000))}m ago`;
  if (h < 48) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function submitTone(row: LifecycleRow): "ok" | "warn" | "bad" | "dim" {
  return lifecycleSubmitTone(row);
}

function submitLabel(row: LifecycleRow): string {
  return lifecycleSubmitLabel(row);
}

function RowDetail({ row }: { row: LifecycleRow }) {
  const links = row.evidence_links;
  const lt = row.last_transition;
  return (
    <div className="space-y-2 border-t border-line bg-bg-elev/40 px-3 py-2 text-xs text-ink-dim">
      <div className="grid gap-2 sm:grid-cols-2">
        <div>
          <span className="text-ink-faint">submit reason </span>
          <span className="mono">{row.submit_reason ?? "—"}</span>
        </div>
        <div>
          <span className="text-ink-faint">next gate </span>
          <span>{row.next_required_gate ?? "—"}</span>
        </div>
        <div>
          <span className="text-ink-faint">evidence age </span>
          <span>{fmtAge(row.latest_revalidation_ts)}</span>
        </div>
        <div>
          <span className="text-ink-faint">transitions </span>
          <span>{row.transition_count ?? 0}</span>
        </div>
      </div>
      {!!row.latest_revalidation_triggers?.length && (
        <div className="flex flex-wrap items-center gap-1">
          <span className="text-ink-faint">triggers</span>
          {row.latest_revalidation_triggers.map((t) => <Badge key={t} tone="warn">{t}</Badge>)}
        </div>
      )}
      {lt && (
        <div>
          <span className="text-ink-faint">last transition </span>
          {lt.from_state} → {lt.to_state}
          {lt.ts ? ` · ${lt.ts.slice(0, 19)}` : ""}
          {lt.reason ? ` · ${lt.reason}` : ""}
        </div>
      )}
      {row.envelope_id && (
        <div><span className="text-ink-faint">envelope </span><span className="mono">{row.envelope_id}</span></div>
      )}
      {links?.research_card_links && Object.keys(links.research_card_links).length > 0 && (
        <div className="flex flex-wrap gap-1">
          <span className="text-ink-faint">research</span>
          {Object.entries(links.research_card_links).map(([k, v]) => (
            <Badge key={k} tone="accent">{k}: {typeof v === "string" ? v : JSON.stringify(v)}</Badge>
          ))}
        </div>
      )}
      <div className="text-[10px] text-ink-faint">read-only registry · no live order routing from workstation</div>
      <OperatorActions modelId={row.id} state={row.state} />
    </div>
  );
}

function OperatorActions({ modelId, state }: { modelId: string; state: string }) {
  const [action, setAction] = useState("request_retest");
  const [reason, setReason] = useState("");
  const [status, setStatus] = useState<string | null>(null);

  async function submit() {
    if (!reason.trim()) {
      setStatus("reason required");
      return;
    }
    setStatus("sending…");
    try {
      const data = await apiPost<{ receipt?: { action?: string } }>("/api/lifecycle/action", {
        model_id: modelId,
        action,
        reason: reason.trim(),
      });
      setStatus(`receipt: ${data?.receipt?.action ?? action}`);
    } catch {
      setStatus("request failed (control scope / local origin required)");
    }
  }

  return (
    <div className="mt-2 space-y-2 rounded border border-line/60 p-2">
      <div className="text-[10px] uppercase tracking-wide text-ink-faint">operator request (receipt only)</div>
      <div className="flex flex-wrap items-center gap-2">
        <select className="rounded border border-line bg-bg-panel px-2 py-1 text-xs" value={action} onChange={(e) => setAction(e.target.value)}>
          <option value="acknowledge_observation">acknowledge observation</option>
          <option value="request_retest">request retest</option>
          <option value="request_quarantine">request quarantine</option>
          <option value="request_rearm">request rearm</option>
          <option value="request_retire">request retire</option>
        </select>
        <input
          className="min-w-[12rem] flex-1 rounded border border-line bg-bg-panel px-2 py-1 text-xs"
          placeholder={`reason (required) · state ${state}`}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
        <button type="button" className="rounded border border-line px-2 py-1 text-xs hover:bg-bg-elev" onClick={() => void submit()}>
          write receipt
        </button>
      </div>
      {status && <div className="text-[10px] text-ink-dim">{status}</div>}
    </div>
  );
}

export function LifecycleView() {
  const z = useZones().lifecycle as LifecycleZone | undefined;
  const [openId, setOpenId] = useState<string | null>(null);

  if (!z) return <Panel title="Model Lifecycle"><span className="text-ink-dim">loading…</span></Panel>;
  if (!z.registered) {
    return (
      <Panel title="Model Lifecycle">
        <div className="rounded-lg border border-line p-3 text-ink-dim">{z.note}</div>
      </Panel>
    );
  }

  return (
    <div className="space-y-5">
      {z.transition_log_warning && (
        <div className="rounded-lg border border-warn/40 bg-warn/5 px-3 py-2 text-sm text-warn">
          {z.transition_log_warning}
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <Kpi label="blocked submit" value={z.blocked_count ?? 0} tone={(z.blocked_count ?? 0) > 0 ? "bad" : "dim"} />
        <Kpi label="degraded" value={z.degraded_count ?? 0} tone={(z.degraded_count ?? 0) > 0 ? "warn" : "dim"} />
        <Kpi label="needs rearm" value={z.needs_rearm_count ?? 0} tone={(z.needs_rearm_count ?? 0) > 0 ? "warn" : "dim"} />
        <Kpi label="needs retest" value={z.needs_retest_count ?? 0} tone="dim" />
        <Kpi label="retired" value={z.retired_count ?? 0} tone="dim" />
        <Kpi label="live" value={z.live} tone={lifecycleLiveKpiTone(z.rows, z.live)} />
      </div>

      <Panel title="Lifecycle funnel" right={`${z.total_models} models · health ${z.health}`}>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
          {LANES.map((s) => (
            <div key={s} className="rounded-lg border border-line bg-bg-elev/50 p-2 text-center">
              <div className="text-[10px] uppercase tracking-wide text-ink-faint">{s}</div>
              <div className="kpi-v text-lg">{z.funnel[s] ?? 0}</div>
            </div>
          ))}
        </div>
      </Panel>

      <Panel title="Models" right="click row for evidence + next gate">
        <div className="scroll-area max-h-[62vh] overflow-auto">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-bg-panel">
              <tr>
                <th className="th w-6" />
                <th className="th">model</th>
                <th className="th">hyp</th>
                <th className="th">sym</th>
                <th className="th">state</th>
                <th className="th">submit</th>
                <th className="th">model_state</th>
                <th className="th">route</th>
                <th className="th">next gate</th>
                <th className="th">evidence</th>
              </tr>
            </thead>
            <tbody>
              {z.rows.map((r) => {
                const expanded = openId === r.id;
                const tone = submitTone(r);
                return (
                  <Fragment key={r.id}>
                    <tr
                      className="cursor-pointer hover:bg-bg-elev/50"
                      onClick={() => setOpenId(expanded ? null : r.id)}
                    >
                      <td className="td text-ink-faint">
                        {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                      </td>
                      <td className="td mono text-xs">{r.id}</td>
                      <td className="td">{r.hypothesis_id ?? "—"}</td>
                      <td className="td">{r.symbol ?? "—"}</td>
                      <td className="td">
                        <span className="inline-flex items-center gap-1.5">
                          <Dot status={r.dot} />
                          {r.state}
                        </span>
                      </td>
                      <td className="td">
                        <Badge tone={tone}>{submitLabel(r)}</Badge>
                      </td>
                      <td className="td">{r.latest_model_state ?? r.last_revalidation ?? "—"}</td>
                      <td className="td text-ink-dim">{r.route ?? "—"}</td>
                      <td className="td text-ink-dim text-xs">{r.next_required_gate ?? "—"}</td>
                      <td className="td text-ink-dim text-xs">{fmtAge(r.latest_revalidation_ts)}</td>
                    </tr>
                    {expanded && (
                      <tr>
                        <td colSpan={10} className="p-0">
                          <RowDetail row={r} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}
