# HFC3 missing MBO data jobs

Generated: 2026-06-07T01:41:50.065231+00:00

Priority order: equity index → rates → metals → energy → FX → vol sensors → warm → cold.

| Priority | Symbol | schema | blocks HOT | proposed command |
|----------|--------|--------|------------|------------------|
| 2 | GC | mbo | True | `python workbench/scripts/backfill_catalog.py --model HYP_5 --symbol GC.v.0 --download-missing --max-cost-usd 25` |
| 2 | HG | mbo | True | `python workbench/scripts/backfill_catalog.py --model HYP_5 --symbol HG.v.0 --download-missing --max-cost-usd 25` |
| 3 | NG | mbo | True | `python workbench/scripts/backfill_catalog.py --model HYP_5 --symbol NG.v.0 --download-missing --max-cost-usd 25` |
| 4 | 6E | mbo | True | `python workbench/scripts/backfill_catalog.py --model HYP_5 --symbol 6E.v.0 --download-missing --max-cost-usd 25` |
| 5 | VX1 | mbo | True | `python workbench/scripts/backfill_catalog.py --model HYP_5 --symbol VX.v.0 --download-missing --max-cost-usd 25` |
| 5 | VX2 | mbo | True | `—` |
| 6 | 6A | mbo | False | `python workbench/scripts/backfill_catalog.py --model HYP_5 --symbol 6A.v.0 --download-missing --max-cost-usd 25` |
| 6 | 6B | mbo | False | `python workbench/scripts/backfill_catalog.py --model HYP_5 --symbol 6B.v.0 --download-missing --max-cost-usd 25` |
| 6 | 6C | mbo | False | `python workbench/scripts/backfill_catalog.py --model HYP_5 --symbol 6C.v.0 --download-missing --max-cost-usd 25` |
| 6 | 6J | mbo | False | `python workbench/scripts/backfill_catalog.py --model HYP_5 --symbol 6J.v.0 --download-missing --max-cost-usd 25` |
| 6 | HO | mbo | False | `python workbench/scripts/backfill_catalog.py --model HYP_5 --symbol HO.v.0 --download-missing --max-cost-usd 25` |
| 6 | RB | mbo | False | `python workbench/scripts/backfill_catalog.py --model HYP_5 --symbol RB.v.0 --download-missing --max-cost-usd 25` |
| 6 | SI | mbo | False | `python workbench/scripts/backfill_catalog.py --model HYP_5 --symbol SI.v.0 --download-missing --max-cost-usd 25` |
| 7 | KE | mbo | False | `python workbench/scripts/backfill_catalog.py --model HYP_5 --symbol KE.v.0 --download-missing --max-cost-usd 25` |
| 7 | ZC | mbo | False | `python workbench/scripts/backfill_catalog.py --model HYP_5 --symbol ZC.v.0 --download-missing --max-cost-usd 25` |
| 7 | ZL | mbo | False | `python workbench/scripts/backfill_catalog.py --model HYP_5 --symbol ZL.v.0 --download-missing --max-cost-usd 25` |
| 7 | ZM | mbo | False | `python workbench/scripts/backfill_catalog.py --model HYP_5 --symbol ZM.v.0 --download-missing --max-cost-usd 25` |
| 7 | ZS | mbo | False | `python workbench/scripts/backfill_catalog.py --model HYP_5 --symbol ZS.v.0 --download-missing --max-cost-usd 25` |
| 7 | ZW | mbo | False | `python workbench/scripts/backfill_catalog.py --model HYP_5 --symbol ZW.v.0 --download-missing --max-cost-usd 25` |
| 9 | MGC | mbo | True | `python workbench/scripts/backfill_catalog.py --model HYP_5 --symbol MGC.v.0 --download-missing --max-cost-usd 25` |