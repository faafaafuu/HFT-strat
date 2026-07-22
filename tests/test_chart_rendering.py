"""Checks that app.js actually draws what the chart data describes.

A plugin can be fully written, have its options set, and still never run if it is left
out of Chart's plugin list — which is exactly how the channel silently disappeared.
The harness stubs canvas and Chart.js, runs the real app.js, and reports the draw calls.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / "tests" / "js" / "chart_harness.js"
APP_JS = ROOT / "app" / "web" / "static" / "js" / "app.js"


def _run_harness() -> dict[str, str]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node не установлен")
    result = subprocess.run(  # noqa: S603 - fixed argv, all paths resolved from the repo
        [node, str(HARNESS), str(APP_JS)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )


def test_channel_plugin_is_registered_and_draws_its_points() -> None:
    output = _run_harness()

    assert "channelBands" in output["PLUGINS"].split(",")
    assert output["POINT_LABELS"] == "1,2,3,4"


def test_candlesticks_are_drawn_when_the_series_has_ohlc() -> None:
    output = _run_harness()

    assert "candlesticks" in output["PLUGINS"].split(",")
    # 200 candles plus the two risk/reward boxes of the single channel.
    assert int(output["CANDLE_BODIES"]) >= 200


def test_channel_boundaries_are_stroked() -> None:
    output = _run_harness()

    # Two boundary lines and a wick per candle.
    assert int(output["STROKES"]) > 200


def test_drag_to_pan_is_wired_up() -> None:
    """The zoom plugin is a UMD global: without an explicit register it never runs."""
    output = _run_harness()

    assert "zoom" in output["GLOBAL_PLUGINS"].split(",")
    assert output["PAN_ENABLED"] == "true"
    assert output["WHEEL_ZOOM"] == "true"
    assert output["CANVAS_CURSOR"] == "grab"
