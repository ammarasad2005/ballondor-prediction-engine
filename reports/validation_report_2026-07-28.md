# Validation Report — Ballon d'Or Prediction Engine

Generated: 2026-07-28T20:22:09.787139+00:00

## Overview

Per Architecture Blueprint §4.6 + Implementation Plan Phase 6:
1. Leave-one-season-out (LOSO) CV
2. Expanding-window CV (train only on seasons BEFORE year Y)
3. Final held-out evaluation (one-shot, per Key Focus Areas §8)

## Held-Out Test Seasons

The following 7 seasons were reserved for one-shot final evaluation:
`[2018, 2019, 2021, 2022, 2023, 2024, 2025]`

Per Key Focus Areas §8, these seasons were NOT used in any feature selection, hyperparameter tuning, or model-selection decision during Phase 5. Evaluated here exactly once.

## LOSO Cross-Validation Results

Per-fold: train on all non-held-out seasons except year Y, predict year Y. Repeat for every season.

| Tier | Top-1 | Top-3 | Top-5 | Spearman ρ | Kendall τ |
|---|---|---|---|---|---|
| A (heuristic) | 32.3% | 48.4% | 64.5% | 0.353 | 0.262 |
| B (pairwise linear) | 33.9% | 50.0% | 67.7% | 0.410 | 0.308 |
| C (XGBoost) | 35.5% | 54.8% | 61.3% | 0.404 | 0.303 |

## Expanding-Window CV Results (Tier B)

More realistic: train only on seasons BEFORE year Y, predict Y.

| Tier | Top-1 | Top-3 | Top-5 | Spearman ρ | Kendall τ |
|---|---|---|---|---|---|
| B (pairwise linear) | 36.5% | 53.8% | 61.5% | 0.386 | 0.288 |

## Final Held-Out Evaluation (One-Shot)

**Per Key Focus Areas §8: this is a one-shot check.** If performance is poor here, that is a final, honestly reported finding, NOT a cue to tune against held-out.

| Tier | Top-1 | Top-3 | Top-5 | Spearman ρ | Kendall τ |
|---|---|---|---|---|---|
| A (heuristic) | 14.3% | 42.9% | 57.1% | 0.468 | 0.341 |
| B (pairwise linear) | 14.3% | 14.3% | 42.9% | 0.523 | 0.377 |
| C (XGBoost) | 28.6% | 28.6% | 42.9% | 0.558 | 0.401 |

## Tier D — Model Selection Decision

Per Architecture Blueprint §4.5 Tier D decision rule:
> Prefer B unless C shows a consistent, non-marginal improvement across multiple validation folds.

LOSO CV comparison (Tier C - Tier B):
- Top-1: 35.5% - 33.9% = +1.6%
- Top-3: 54.8% - 50.0% = +4.8%

Tier C shows marginal improvement (+1.6% top-1). Per Tier D decision rule, **Tier B is selected** for interpretability. Tier C as secondary.

**Selected primary model: Tier B**

## Per-Era Breakdown (LOSO CV, Tier B)

Per Architecture Blueprint P4, performance should differ by era by design.

| Era | Years | Top-1 | Top-3 | Top-5 |
|---|---|---|---|---|
| classical | 39 | 23.1% | 43.6% | 66.7% |
| pre_merger | 15 | 33.3% | 40.0% | 53.3% |
| fifa_merger | 6 | 83.3% | 100.0% | 100.0% |
| post_split | 2 | 100.0% | 100.0% | 100.0% |

## Conclusion

Selected model: **Tier B**. See reports/validation_metrics.json for full per-fold results.

Held-out evaluation is honest, one-shot, never tuned against. The gap between LOSO CV and held-out performance (if any) reflects natural generalization cost — not a tuning opportunity.
