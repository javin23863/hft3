// Dependency-free inline SVG sparkline for the cumulative PnL preview.
export function Sparkline({ values, width = 240, height = 44 }: { values: number[]; width?: number; height?: number }) {
  if (!values.length) return <div className="muted">no series</div>;
  let cum = 0;
  const series = values.map((v) => (cum += v));
  const min = Math.min(...series, 0);
  const max = Math.max(...series, 0);
  const span = max - min || 1;
  const dx = width / Math.max(series.length - 1, 1);
  const pts = series.map((v, i) => `${(i * dx).toFixed(1)},${(height - ((v - min) / span) * height).toFixed(1)}`);
  const last = series[series.length - 1];
  const stroke = last >= 0 ? "var(--green)" : "var(--red)";
  const zeroY = (height - ((0 - min) / span) * height).toFixed(1);
  return (
    <svg width={width} height={height} style={{ display: "block" }}>
      <line x1="0" y1={zeroY} x2={width} y2={zeroY} stroke="var(--border)" strokeDasharray="2 3" />
      <polyline points={pts.join(" ")} fill="none" stroke={stroke} strokeWidth="1.5" />
    </svg>
  );
}
