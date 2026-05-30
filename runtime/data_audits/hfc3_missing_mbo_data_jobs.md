# HFC3 missing MBO data jobs

Generated: 2026-05-30T15:51:25.937135+00:00

Priority order: equity index → rates → metals → energy → FX → vol sensors → warm → cold.

| Priority | Symbol | schema | blocks HOT | proposed command |
|----------|--------|--------|------------|------------------|
| 0 | NQ | mbo | True | `python workbench/scripts/backfill_catalog.py --model HYP_5 --symbol NQ.v.0 --download-missing --max-cost-usd 25` |
| 0 | RTY | mbo | True | `python workbench/scripts/backfill_catalog.py --model HYP_5 --symbol RTY.v.0 --download-missing --max-cost-usd 25` |
| 0 | YM | mbo | True | `python workbench/scripts/backfill_catalog.py --model HYP_5 --symbol YM.v.0 --download-missing --max-cost-usd 25` |
| 1 | SR3 | mbo | True | `python workbench/scripts/backfill_catalog.py --model HYP_5 --symbol SR3.v.0 --download-missing --max-cost-usd 25` |
| 1 | UB | mbo | True | `python workbench/scripts/backfill_catalog.py --model HYP_5 --symbol UB.v.0 --download-missing --max-cost-usd 25` |
| 1 | ZB | mbo | True | `python workbench/scripts/backfill_catalog.py --model HYP_5 --symbol ZB.v.0 --download-missing --max-cost-usd 25` |
| 1 | ZF | mbo | True | `python workbench/scripts/backfill_catalog.py --model HYP_5 --symbol ZF.v.0 --download-missing --max-cost-usd 25` |
| 1 | ZN | mbo | True | `python workbench/scripts/backfill_catalog.py --model HYP_5 --symbol ZN.v.0 --download-missing --max-cost-usd 25` |
| 1 | ZQ | mbo | True | `python workbench/scripts/backfill_catalog.py --model HYP_5 --symbol ZQ.v.0 --download-missing --max-cost-usd 25` |
| 1 | ZT | mbo | True | `python workbench/scripts/backfill_catalog.py --model HYP_5 --symbol ZT.v.0 --download-missing --max-cost-usd 25` |
| 2 | GC | mbo | True | `python workbench/scripts/backfill_catalog.py --model HYP_5 --symbol GC.v.0 --download-missing --max-cost-usd 25` |
| 2 | HG | mbo | True | `python workbench/scripts/backfill_catalog.py --model HYP_5 --symbol HG.v.0 --download-missing --max-cost-usd 25` |
| 3 | CL | mbo | True | `python workbench/scripts/backfill_catalog.py --model HYP_5 --symbol CL.v.0 --download-missing --max-cost-usd 25` |
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
| 9 | M2K | mbo | True | `python workbench/scripts/backfill_catalog.py --model HYP_5 --symbol M2K.v.0 --download-missing --max-cost-usd 25` |
| 9 | MCL | mbo | True | `python workbench/scripts/backfill_catalog.py --model HYP_5 --symbol MCL.v.0 --download-missing --max-cost-usd 25` |
| 9 | MGC | mbo | True | `python workbench/scripts/backfill_catalog.py --model HYP_5 --symbol MGC.v.0 --download-missing --max-cost-usd 25` |
| 9 | MNQ | mbo | True | `python workbench/scripts/backfill_catalog.py --model HYP_5 --symbol MNQ.v.0 --download-missing --max-cost-usd 25` |
| 9 | MYM | mbo | True | `python workbench/scripts/backfill_catalog.py --model HYP_5 --symbol MYM.v.0 --download-missing --max-cost-usd 25` |