#!/usr/bin/env python3
"""Generate C++ label + regime tables from event_universe.yaml."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from economic_event_universe.registry import event_definitions
from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

PKG = _REPO / "packages" / "features_engine"
OUT_HPP = PKG / "cpp" / "include" / "event_context_labels.generated.hpp"
OUT_REGIME_HPP = PKG / "cpp" / "include" / "event_context_regime.generated.hpp"
OUT_JSON = PKG / "config" / "event_context_labels.json"


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def main() -> int:
    defs = event_definitions()
    table: dict[str, dict[str, str | int]] = {}
    shock: list[str] = []
    flatten: list[str] = []
    for et, cfg in sorted(defs.items()):
        label = str(cfg.get("event_context_label", "") or "")
        main_label = str(cfg.get("main_context_label", "") or "")
        table[et] = {
            "label": label,
            "main_label": main_label,
            "context_priority": int(cfg.get("context_priority", 50)),
        }
        rc = str(cfg.get("regime_class", "none"))
        boost = bool(cfg.get("regime_boost", False))
        if boost and rc == "event_shock" and label:
            shock.append(label)
        elif boost and rc == "prop_flatten" and label:
            flatten.append(label)

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(table, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    entries = [
        f'        {{"{et}", {{"{_esc(str(row["label"]))}", "{_esc(str(row["main_label"]))}", {int(row["context_priority"])}}},}}'
        for et, row in table.items()
    ]
    OUT_HPP.write_text(
        f"""#pragma once
// AUTO-GENERATED — python tools/economic_event_universe/generate_event_context_labels.py

#include <string>
#include <unordered_map>

namespace hft {{

struct EventContextLabelEntry {{
    std::string label;
    std::string main_label;
    int context_priority{{50}};
}};

inline const std::unordered_map<std::string, EventContextLabelEntry>& event_context_label_map() {{
    static const std::unordered_map<std::string, EventContextLabelEntry> kMap = {{
{chr(10).join(entries)}
    }};
    return kMap;
}}

}}  // namespace hft
""",
        encoding="utf-8",
    )

    def _arr(labels: list[str]) -> str:
        return ",\n        ".join(f'"{_esc(x)}"' for x in sorted(set(labels)))

    OUT_REGIME_HPP.write_text(
        f"""#pragma once
// AUTO-GENERATED — regime boost label sets from event_universe.yaml

#include <string_view>

namespace hft {{

inline constexpr std::string_view kEventShockLabels[] = {{
        {_arr(shock)}
}};

inline constexpr std::string_view kPropFlattenLabels[] = {{
        {_arr(flatten)}
}};

}}  // namespace hft
""",
        encoding="utf-8",
    )
    regime_json = {"event_shock": sorted(set(shock)), "prop_flatten": sorted(set(flatten))}
    (PKG / "config" / "event_context_regime.json").write_text(
        json.dumps(regime_json, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Wrote {OUT_HPP} ({len(table)} types, {len(shock)} shock, {len(flatten)} flatten)")
    print(f"Wrote {OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
