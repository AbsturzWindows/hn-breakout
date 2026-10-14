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
