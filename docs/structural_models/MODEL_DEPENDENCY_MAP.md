# PDF model dependency map

Total inventory: **44 HYP_*** + **7 PDF_MODEL_*** = **51**.

Only two internal convergences (combined math inside one registry entry):

| Model | Convergence |
|-------|-------------|
| PDF_MODEL_1 | OFI + MLOFI + PCA + spoof defense |
| PDF_MODEL_4 | Avellaneda-Stoikov + reads Model 1 OFI_smooth + Model 3 VPIN |

## Directed dependencies (runtime reads)

```
PDF_MODEL_1 (BookPressure)
    ├── PDF_MODEL_2 (CrossAssetLeadLag)   [reads Model 1 outputs]
    ├── PDF_MODEL_4 (HybridExecution)       [reads Model 1 OFI_smooth]
    └── PDF_MODEL_6 (DowYMIndex)            [reads Model 1 per constituent]

PDF_MODEL_3 (VPIN)
    └── PDF_MODEL_4 (HybridExecution)       [reads Model 3 VPIN_value]

PDF_MODEL_5 (DealerGEX)          — standalone
PDF_MODEL_7 (TreasuryCTD)        — standalone
```

## Explicit non-combinations

Do **not** merge in one codebase:

- VPIN + OFI (except Model 4 consumption)
- GEX + OFI
- CTD + Dow
- AS + signal discovery (HYP evaluate path)

## Registry separation

| API | Returns |
|-----|---------|
| `get_active_hypotheses()` | 44 HYP families (39 active without cross-asset env) |
| `get_structural_models()` | 7 PDF_MODEL instances |

PDF outputs live in `ModelOutput` dataclasses, not 64-dim `FeatureIndex` slots.
