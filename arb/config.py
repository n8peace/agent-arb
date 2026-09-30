"""Tunable constants for the decision rules. Integer cents and whole percents only."""

# Clamped mechanism (decide.clamped): the panel's owed range counts as "narrow", and
# owed.point is awarded outright, when high - low <= the larger of these two:
CLAMP_NARROW_MIN_CENTS = 5000   # $50
CLAMP_NARROW_PCT = 5            # 5% of the contested amount (owed.high unless the caller passes one)
