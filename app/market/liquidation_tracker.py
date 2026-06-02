from __future__ import annotations


class LiquidationTracker:
    """Optional v2 component.

    If a venue does not provide a reliable public liquidation feed, fields stay
    unavailable instead of being fabricated.
    """

