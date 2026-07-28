# xG/xA Integration Validation Report

Generated: 2026-07-28T21:13:42.370847+00:00

## Overview

Per user request, integrated Understat as an alternative xG/xA source (fbref was Cloudflare-blocked). This report compares model performance with the ORIGINAL feature set vs the EXPANDED feature set (adding xG, xA, npxG, xg_per90, xa_per90, xg_overperformance).

Per Key Focus Areas §8: held-out evaluation is one-shot. We are NOT tuning against held-out — just honestly comparing two feature sets.

## LOSO CV Results (62 folds)

| Model | Top-1 (orig) | Top-1 (exp) | Δ | Top-3 (orig) | Top-3 (exp) | Δ | Spearman (orig) | Spearman (exp) |
|---|---|---|---|---|---|---|---|---|
| Tier B | 33.9% | 33.9% | +0.0% | 50.0% | 50.0% | +0.0% | 0.410 | 0.405 |
| Tier C | 35.5% | 35.5% | +0.0% | 54.8% | 53.2% | -1.6% | 0.404 | 0.403 |

## Held-Out Evaluation Results (one-shot, 7 seasons)

| Model | Top-1 (orig) | Top-1 (exp) | Δ | Top-3 (orig) | Top-3 (exp) | Δ | Spearman (orig) | Spearman (exp) |
|---|---|---|---|---|---|---|---|---|
| Tier B | 14.3% | 14.3% | +0.0% | 14.3% | 42.9% | +28.6% | 0.523 | 0.507 |
| Tier C | 28.6% | 28.6% | +0.0% | 28.6% | 28.6% | +0.0% | 0.558 | 0.536 |

## Interpretation

- Tier B LOSO CV: xG/xA addition changed top-1 by +0.0% and top-3 by +0.0%
- Tier C LOSO CV: xG/xA addition changed top-1 by +0.0%
- Tier B held-out: xG/xA addition changed top-1 by +0.0%
- Tier C held-out: xG/xA addition changed top-1 by +0.0%

## Decision

❌ **xG/xA did not improve generalization.** Document the negative result honestly and keep features available for future use.
