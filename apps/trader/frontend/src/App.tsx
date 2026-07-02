import { useEffect, useState } from "react";

type Evidence = { path: string; sha256: string; age_seconds: number | null; status: string; reason: string };
type Blocked = { blocked: true; reason: string; evidence?: Evidence };

type Funnel = {
  blocked: false;
  stages: string[];
  totals: Record<string, number>;
  per_model: Record<string, Record<string, number>>;
  evidence: Evidence;
};

type ModelRow = {
  canonical_model_id: string;
  runs: number;
  event_count: number;
  realized_run_count: number;
  realized_total: number | null;
  realized_mean: number | null;
  realized_min: number | null;
  realized_max: number | null;
  gate3_any_pass: boolean;
  gate4_any_pass: boolean;
  promotion_any_allowed: boolean;
};

type RunDetail = {
  run_id: string;
  event_id: string;
  strategy_params: Record<string, unknown>;
  realized_closed_trade_pnl: number | null;
  unrealized_pnl_marked_to_mid: number | null;
  end_position: number | null;
  exit_reason: string;
  fill_rate: number | null;
  gate3_status: string;
  gate3_axes_unavailable_upstream: string[];
  gate4_status: string;
  gate4_psr: number | null;
  gate4_dsr: number | null;
  gate4_pbo: number | null;
  promotion_decision: string;
  promotion_allowed: boolean;
  fail_closed_reasons: string[];
  receipt: { artifact_dir: string; stats_summary_sha256: string };
};

async function apiGet<T>(path: string): Promise<T | Blocked> {
  const res = await fetch(path);
  if (!res.ok) return { blocked: true, reason: `http_${res.status}` };
  return res.json();
}

function EvidenceLine({ e }: { e?: Evidence }) {
  if (!e) return null;
  const age = e.age_seconds != null ? `${Math.round(e.age_seconds)}s ago` : "unknown age";
  return (
    <div className="evidence">
      receipt: {e.path} · sha256 {e.sha256 ? e.sha256.slice(0, 16) + "…" : "—"} · {age}
    </div>
  );
}

function BlockedBanner({ b }: { b: Blocked }) {
  return (
    <div>
      <div className="blocked">BLOCKED — {b.reason}</div>
      <EvidenceLine e={b.evidence} />
    </div>
  );
}

function Pnl({ v }: { v: number | null }) {
  if (v == null) return <span className="badge none">n/a</span>;
  return <span className={v >= 0 ? "pos" : "neg"}>{v.toFixed(2)}</span>;
}

function GateBadge({ s }: { s: string }) {
  if (s === "pass") return <span className="badge pass">PASS</span>;
  if (s === "fail") return <span className="badge fail">FAIL</span>;
  return <span className="badge none">not run</span>;
}

function FunnelView() {
  const [data, setData] = useState<Funnel | Blocked | null>(null);
  useEffect(() => {
    apiGet<Funnel>("/api/funnel").then(setData);
  }, []);
  if (!data) return <div className="panel">loading…</div>;
  if (data.blocked) return <BlockedBanner b={data} />;
  const labels: Record<string, string> = {
    runs: "Runs",
    orders_submitted: "Orders",
    realized_pnl: "Realized PnL",
    economic_pass: "Economic pass",
    gate3_pass: "Gate 3 pass",
    gate4_pass: "Gate 4 pass",
    promotion_allowed: "Promotable",
  };
  return (
    <div className="panel">
      <div className="funnel">
        {data.stages.map((s) => (
          <div className="stage" key={s}>
            <div className="n">{data.totals[s]}</div>
            <div className="label">{labels[s] ?? s}</div>
          </div>
        ))}
      </div>
      <EvidenceLine e={data.evidence} />
    </div>
  );
}

function ModelsView({ onOpen }: { onOpen: (id: string) => void }) {
  const [data, setData] = useState<{ blocked: false; models: ModelRow[]; evidence: Evidence } | Blocked | null>(null);
  useEffect(() => {
    apiGet<{ blocked: false; models: ModelRow[]; evidence: Evidence }>("/api/models").then(setData);
  }, []);
  if (!data) return <div className="panel">loading…</div>;
  if (data.blocked) return <BlockedBanner b={data} />;
  return (
    <div className="panel">
      <table>
        <thead>
          <tr>
            <th>Model</th><th>Runs</th><th>Events</th><th>Σ realized</th><th>mean</th>
            <th>min</th><th>max</th><th>G3</th><th>G4</th><th>Promo</th>
          </tr>
        </thead>
        <tbody>
          {data.models.map((m) => (
            <tr key={m.canonical_model_id} className="clickable" onClick={() => onOpen(m.canonical_model_id)}>
              <td className="mono">{m.canonical_model_id}</td>
              <td>{m.runs}</td>
              <td>{m.event_count}</td>
              <td><Pnl v={m.realized_total} /></td>
              <td><Pnl v={m.realized_mean} /></td>
              <td><Pnl v={m.realized_min} /></td>
              <td><Pnl v={m.realized_max} /></td>
              <td><GateBadge s={m.gate3_any_pass ? "pass" : ""} /></td>
              <td><GateBadge s={m.gate4_any_pass ? "pass" : ""} /></td>
              <td><GateBadge s={m.promotion_any_allowed ? "pass" : ""} /></td>
            </tr>
          ))}
        </tbody>
      </table>
      <EvidenceLine e={data.evidence} />
    </div>
  );
}

function ModelDetailView({ modelId, onBack }: { modelId: string; onBack: () => void }) {
  const [data, setData] = useState<
    { blocked: false; canonical_model_id: string; runs: RunDetail[]; evidence: Evidence } | Blocked | null
  >(null);
  useEffect(() => {
    apiGet<{ blocked: false; canonical_model_id: string; runs: RunDetail[]; evidence: Evidence }>(
      `/api/models/${encodeURIComponent(modelId)}`,
    ).then(setData);
  }, [modelId]);
  if (!data) return <div className="panel">loading…</div>;
  if (data.blocked) return <BlockedBanner b={data} />;
  return (
    <div className="panel">
      <button onClick={onBack}>← models</button>
      <h3 className="mono">{data.canonical_model_id}</h3>
      <table>
        <thead>
          <tr>
            <th>Event</th><th>Realized</th><th>Exit</th><th>Fill rate</th>
            <th>G3</th><th>G4</th><th>PSR</th><th>DSR</th><th>PBO</th><th>Decision</th>
          </tr>
        </thead>
        <tbody>
          {data.runs.map((r) => (
            <tr key={r.run_id}>
              <td className="mono">{r.event_id}</td>
              <td><Pnl v={r.realized_closed_trade_pnl} /></td>
              <td>{r.exit_reason || "—"}</td>
              <td>{r.fill_rate != null ? r.fill_rate.toFixed(2) : "—"}</td>
              <td><GateBadge s={r.gate3_status} /></td>
              <td><GateBadge s={r.gate4_status} /></td>
              <td>{r.gate4_psr != null ? r.gate4_psr.toFixed(3) : "—"}</td>
              <td>{r.gate4_dsr != null ? r.gate4_dsr.toFixed(3) : "—"}</td>
              <td>{r.gate4_pbo != null ? r.gate4_pbo.toFixed(3) : "—"}</td>
              <td>{r.promotion_decision || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {data.runs.map(
        (r) =>
          r.gate3_axes_unavailable_upstream.length > 0 && (
            <div className="note" key={r.run_id + "-axes"}>
              {r.run_id}: Gate 3 passed with upstream-unavailable axes ({r.gate3_axes_unavailable_upstream.length}) — review before certification.
            </div>
          ),
      )}
      {data.runs.map((r) => (
        <div className="evidence" key={r.run_id + "-rcpt"}>
          {r.run_id}: {r.receipt.artifact_dir} · stats sha256 {r.receipt.stats_summary_sha256.slice(0, 16)}…
        </div>
      ))}
    </div>
  );
}

function CampaignView() {
  const [data, setData] = useState<any>(null);
  useEffect(() => {
    apiGet<any>("/api/campaign").then(setData);
  }, []);
  if (!data) return <div className="panel">loading…</div>;
  if (data.blocked) return <BlockedBanner b={data} />;
  return (
    <div className="panel">
      {data.note && <div className="note">{data.note}</div>}
      {(data.receipts ?? []).map((r: any, i: number) => (
        <div key={i}>
          <pre className="mono">{JSON.stringify(r.summary, null, 1)}</pre>
          <EvidenceLine e={r.evidence} />
        </div>
      ))}
    </div>
  );
}

function LifecycleView() {
  const [data, setData] = useState<any>(null);
  useEffect(() => {
    apiGet<any>("/api/lifecycle").then(setData);
  }, []);
  if (!data) return <div className="panel">loading…</div>;
  if (data.blocked) return <BlockedBanner b={data} />;
  if (data.empty_state)
    return (
      <div className="panel">
        <div className="note">{data.note}</div>
        <EvidenceLine e={data.evidence} />
      </div>
    );
  return (
    <div className="panel">
      <table>
        <thead>
          <tr><th>Model</th><th>State</th><th>Symbol</th><th>Envelope</th></tr>
        </thead>
        <tbody>
          {data.models.map((m: any) => (
            <tr key={m.model_id}>
              <td className="mono">{m.model_id}</td>
              <td>{m.state}</td>
              <td>{m.symbol}</td>
              <td className="mono">{m.envelope_id}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <EvidenceLine e={data.evidence} />
    </div>
  );
}

const TABS = ["Funnel", "Models", "Campaign", "Lifecycle"] as const;

export default function App() {
  const [tab, setTab] = useState<(typeof TABS)[number]>("Funnel");
  const [modelId, setModelId] = useState<string | null>(null);
  return (
    <div className="app">
      <div className="header">
        <h1>HFT3 Trader</h1>
        <span className="sub">every number traces to a receipt · read-only · fail-closed</span>
      </div>
      <div className="tabs">
        {TABS.map((t) => (
          <button
            key={t}
            className={tab === t && !modelId ? "active" : ""}
            onClick={() => {
              setTab(t);
              setModelId(null);
            }}
          >
            {t}
          </button>
        ))}
      </div>
      {modelId ? (
        <ModelDetailView modelId={modelId} onBack={() => setModelId(null)} />
      ) : tab === "Funnel" ? (
        <FunnelView />
      ) : tab === "Models" ? (
        <ModelsView onOpen={setModelId} />
      ) : tab === "Campaign" ? (
        <CampaignView />
      ) : (
        <LifecycleView />
      )}
    </div>
  );
}
