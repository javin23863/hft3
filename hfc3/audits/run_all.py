"""Generate all HFC3 audit deliverables."""

from __future__ import annotations

from hfc3.audits.mbo_inventory import write_inventory
from hfc3.audits.repo_audit import write_audit


def main() -> None:
    md1, js1 = write_audit()
    print(f"Phase 1: {md1}, {js1}")
    paths = write_inventory()
    print(f"Phase 2+9: {paths}")


if __name__ == "__main__":
    main()
