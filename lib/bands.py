#!/usr/bin/env python3
"""
bands.py — conviction sizing thresholds, and nothing else.

These live apart from config.py for one reason: the dashboard needs them and
must not import config.py. config.py reads broker and Oracle credentials out
of the environment at import time, so pulling it into the UI layer couples a
colour choice to the presence of a Kite API key.

That coupling is not hypothetical. The badge on the dashboard has now twice
drifted out of step with the policy it claims to show — first still banding on
the sizing buckets after sizing was disabled, then still banding on engine
tiers after it was re-enabled at different cutoffs. Both times the number
rendered was right and its colour was making a false claim about what would
happen to the money.

So there is exactly one definition, with no import-time dependencies, and both
the bot and the UI read it from here. Change a threshold and the badge follows
automatically.

config.py re-exports these, so `from lib.config import CONVICTION_SIZING`
keeps working everywhere it is already used.
"""

# See lib/config.py for the full derivation and its caveats — in short, these
# are percentile-matched to lib/conviction_lite.py's score distribution, and
# are NOT portable to a different scoring engine.
CONVICTION_SIZING_ENABLED = True

CONVICTION_SIZING = [
    (92, 25000),   # score > 92        -> Rs 25,000
    (69, 10000),   # 69 <= score <= 92 -> Rs 10,000
]

# Pinned to the lower band, and kept at or above conviction_lite.ACCEPT_FLOOR
# so sizing never funds a name the engine's own verdict rejects.
CONVICTION_MIN_SCORE = 69

# Recalibrated 2026-08-29 from 85/63, when conviction_lite replaced its
# 'upside' component with 'reachability'. Changing a component changes the
# score distribution, and the old cutoffs would have kept their numbers while
# silently becoming a different policy — 21% of names in the Rs 25,000 band
# against the 7% intended. Achieved at 92/69: 7.0% / 37.2% / 55.8% against an
# intent of 7.2% / 36.6% / 56.2%.
#
# Re-run tools/recalibrate_bands.py after ANY change to the engine's
# components or weights. This is the third time these thresholds have needed
# it; treat them as derived, never as constants.

# Fallback banding for display when sizing is disabled, or when a score came
# from a different engine: the engine's own tiers, which are what a score
# means when it is not deciding anything. Mirrors conviction_lite.TIERS.
DISPLAY_TIERS = (80, 65, 50)

# Which engine's distribution the thresholds above were matched to. A score
# from any other engine must NOT be shown as if these bands applied to it —
# they are percentile statements about one distribution, and the full
# engine's scores cluster in 50-87 where 85/63 is close to meaningless.
SIZING_MODEL = 'lite'


def size_for(score):
    """Position size a score would receive, or None if it would not be bought.

    The single source of truth for 'what does this number do', used by the
    buy path and by the dashboard badge so the two cannot disagree.
    """
    if score is None or score != score:      # None or NaN
        return None
    if not CONVICTION_SIZING_ENABLED:
        return None
    v = float(score)
    if v < CONVICTION_MIN_SCORE:
        return None
    for floor, amount in CONVICTION_SIZING:
        if v > floor:
            return amount
    return CONVICTION_SIZING[-1][1]
