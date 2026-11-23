import pandas as pd
import pytest

from hn_breakout.train import precision_at_10


def test_precision_at_10_is_averaged_per_day():
    df = pd.DataFrame(
        {"day": ["d1"] * 12 + ["d2"] * 10, "hit": [1] * 10 + [0, 0] + [1] * 5 + [0] * 5}
    )
    score = list(range(12, 0, -1)) + list(range(10, 0, -1))  # day 1: its 10 hits score highest
    assert precision_at_10(df, score) == pytest.approx((1.0 + 0.5) / 2)
