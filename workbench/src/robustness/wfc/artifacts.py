"""WFC visualization and report artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from workbench.src.robustness.wfc.gate import WfcResult
from workbench.src.robustness.wfc.metrics import metric_value


def write_wfc_artifacts(
    out_dir: Path,
    result: WfcResult,
    rows: List[Dict[str, Any]],
    *,
    primary_metric: str = "sharpe",
) -> List[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: List[str] = []

    summary_path = out_dir / "wfc_summary.json"
    summary_path.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
    paths.append(str(summary_path))

    audit_lines = [
        f"WFC status: {result.wfc_status}",
        f"Pearson: {result.pearson:.4f} CI {result.pearson_ci}",
        f"Spearman: {result.spearman:.4f} CI {result.spearman_ci}",
        f"p-value: {result.p_value:.4f}",
        f"fold_correlations: {result.fold_correlations}",
    ]
    if result.rejection_reasons:
        audit_lines.append("Rejection reasons:")
        audit_lines.extend(f"  - {r}" for r in result.rejection_reasons)
    audit_path = out_dir / "wfc_audit.log"
    audit_path.write_text("\n".join(audit_lines) + "\n", encoding="utf-8")
    paths.append(str(audit_path))

    if not rows:
        result.artifact_paths = paths
        return paths

    try:
        import matplotlib.pyplot as plt
        import numpy as np

        is_vals = [metric_value(r.get("is_metrics", {}), primary_metric) for r in rows]
        oos_vals = [metric_value(r.get("oos_metrics", {}), primary_metric) for r in rows]

        fig, ax = plt.subplots(figsize=(7, 6))
        ax.scatter(is_vals, oos_vals, alpha=0.5, s=12)
        if len(is_vals) >= 2:
            coef = np.polyfit(is_vals, oos_vals, 1)
            xs = np.linspace(min(is_vals), max(is_vals), 50)
            ax.plot(xs, coef[0] * xs + coef[1], "r--", lw=1)
        ax.set_xlabel(f"IS {primary_metric}")
        ax.set_ylabel(f"OOS {primary_metric}")
        ax.set_title(f"WFC r={result.pearson:.3f} rho={result.spearman:.3f}")
        scatter_path = out_dir / "is_vs_oos_scatter.png"
        fig.tight_layout()
        fig.savefig(scatter_path, dpi=120)
        plt.close(fig)
        paths.append(str(scatter_path))

        fig2, ax2 = plt.subplots(figsize=(7, 6))
        folds = sorted({str(r.get("fold_id", "all")) for r in rows})
        colors = plt.cm.tab10(np.linspace(0, 1, max(len(folds), 1)))
        for color, fid in zip(colors, folds):
            fold_rows = [r for r in rows if str(r.get("fold_id")) == fid]
            fx = [metric_value(r.get("is_metrics", {}), primary_metric) for r in fold_rows]
            fy = [metric_value(r.get("oos_metrics", {}), primary_metric) for r in fold_rows]
            ax2.scatter(fx, fy, alpha=0.5, s=12, label=fid, c=[color])
        ax2.legend(fontsize=8)
        ax2.set_xlabel(f"IS {primary_metric}")
        ax2.set_ylabel(f"OOS {primary_metric}")
        fold_path = out_dir / "is_vs_oos_by_fold.png"
        fig2.tight_layout()
        fig2.savefig(fold_path, dpi=120)
        plt.close(fig2)
        paths.append(str(fold_path))

        fig3, ax3 = plt.subplots(figsize=(6, 4))
        ax3.boxplot(
            [
                [metric_value(r.get("oos_metrics", {}), primary_metric) for r in rows],
            ],
            labels=["all"],
        )
        ax3.axhline(result.bottom_decile_oos_median, color="red", ls="--", label="bottom decile")
        ax3.axhline(result.top_decile_oos_median, color="green", ls="--", label="top decile")
        decile_path = out_dir / "top_decile_oos.png"
        fig3.tight_layout()
        fig3.savefig(decile_path, dpi=120)
        plt.close(fig3)
        paths.append(str(decile_path))

        param_keys = set()
        for r in rows:
            param_keys.update((r.get("params") or {}).keys())
        if len(param_keys) >= 2:
            keys = sorted(param_keys)[:2]
            k0, k1 = keys[0], keys[1]
            xs = [float(r["params"][k0]) for r in rows if k0 in r.get("params", {})]
            ys = [float(r["params"][k1]) for r in rows if k1 in r.get("params", {})]
            zs = oos_vals[: len(xs)]
            if xs and ys:
                fig4, ax4 = plt.subplots(figsize=(7, 5))
                sc = ax4.scatter(xs, ys, c=zs, cmap="viridis", s=15)
                fig4.colorbar(sc, label=f"OOS {primary_metric}")
                ax4.set_xlabel(k0)
                ax4.set_ylabel(k1)
                surf_path = out_dir / "stability_surface.png"
                fig4.tight_layout()
                fig4.savefig(surf_path, dpi=120)
                plt.close(fig4)
                paths.append(str(surf_path))
    except Exception:
        pass

    result.artifact_paths = paths
    summary_path.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
    return paths
