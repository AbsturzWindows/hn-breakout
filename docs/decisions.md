# Decisions

Decisions fixed before training, so they are visibly not tuned on results.

## 2026-10-14: Hit threshold stays at 100 points

Rule from the proposal: if fewer than 2 % of stories reach 100 points, use 50 points instead.

Estimate from Algolia (`scripts/estimate_hit_rate.py`, stories from 2026-09-14 to 2026-10-12):

| | |
|---|---|
| Stories per day | 988 |
| Reach >= 100 points | 4.29 % (about 594 hits in 2 test weeks) |
| Reach >= 50 points | 6.57 % |

4.29 % is above 2 %, so the label stays: **final score >= 100 points**.

Note: the poller sees about 1,200 stories per day, about 20 % more than Algolia. The hit
rate on the poller's own labels will be compared with this estimate once a few days of
labels exist. The threshold does not change based on that comparison.


## 2026-10-17: Daily top 10 alerts

- Evaluation: per UTC calendar day, all stories posted that day are ranked by their predicted
  probability at 30 minutes. Precision of the top 10 is computed after the day has ended and
  averaged over the test days. The baseline is ranked the same way by points at 30 minutes.
- Live: the day is not over yet, so the UI alerts on a fixed probability threshold, chosen on
  the training data so that about 10 stories per day are above it.

## 2026-11-22: domain_hit_rate uses only the poller's own 24-hour labels (review point B1)

The backfilled `history` table stores Algolia points from the day of the backfill, not after
24 hours. `scripts/compare_labels.py` compares both labels on the same 15,868 stories
(14.-28.10.2026):

| | |
|---|---|
| Hit rate, 24-hour label | 3.34 % |
| Hit rate, Algolia points | 4.15 % |
| Domains with >= 20 stories | 55: Algolia higher in 16, equal in 39, lower in none |

The history label is never lower and often higher, so it would make domains look better than
the 24-hour target, and the feature would change its meaning on 13.10. Decision: `domain_hit_rate`
uses only the poller's labels. Domains with few labels fall back to the overall rate
(`PRIOR_WEIGHT` in `features.py`), so the feature is weak in the first days and improves as labels
accumulate. The `history` table stays in the bucket but is not used.

## 2026-11-22: Train, validation and test periods (review point A4)

Fixed before any model is trained. Days are UTC calendar days by posting time.

| Period | Days | Used for |
|---|---|---|
| Training | 13.10.–08.11.2026 | fitting the models |
| Validation | 09.11.–15.11.2026 (Mon–Sun) | early stopping, the live alert threshold (B2), any tuning |
| Test | 16.11.–29.11.2026 (two full weeks, Mon–Sun) | the final MS3 numbers, touched once per model |

- The test period ends on 29.11. so that its labels (24 h) and the daily feature build are
  complete before training on 01.12.
- Full weeks keep weekdays and weekends balanced, since weekends have fewer stories and more hits.
- `domain_hit_rate` for test stories may use labels from the training period, as it would live;
  it never uses a label known after the story's prediction time (A1).
- The proposal planned six weeks of training; the poller started on 13.10., so it is five.
