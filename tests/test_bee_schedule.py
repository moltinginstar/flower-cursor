"""The one wait between visits, and the switches that set or silence it."""

import math
import random

import pytest

from flower_cursor.bee import DEFAULT_INTERVAL, INTERVAL_JITTER, IdleBee, NoBee
from flower_cursor.geometry import ViewportGeometry
from flower_cursor.lifecycle import CursorCell
from flower_cursor.observer import build_parser
from flower_cursor.settings import (
    BEE_INTERVAL_DEFAULT,
    BEE_INTERVAL_LIMIT,
    BEE_INTERVAL_VARIABLE,
    BEE_VARIABLE,
    bee_enabled,
    bee_interval,
)

VIEWPORT = ViewportGeometry(30, 100, 1600, 990)
HEAD = (192.0, 250.0)


def run(bee: IdleBee, seconds: float) -> None:
    """Advance a whole wait the way the observer does, a frame at a time."""
    for _ in range(round(seconds * 60)):
        bee.update(1 / 60, HEAD)


def settled(interval: float = 20.0, seed: int = 5) -> IdleBee:
    bee = IdleBee(random.Random(seed), interval=interval)
    bee.set_geometry(VIEWPORT, (50, 45))
    bee.move_to(CursorCell(8, 12))
    return bee


def test_the_wait_is_drawn_fresh_and_lands_inside_the_jitter():
    waits = set()
    for seed in range(40):
        bee = IdleBee(random.Random(seed), interval=DEFAULT_INTERVAL)
        assert (
            DEFAULT_INTERVAL * INTERVAL_JITTER[0]
            <= bee.wait
            <= DEFAULT_INTERVAL * INTERVAL_JITTER[1]
        )
        waits.add(bee.wait)
    # A visitor on a fixed schedule reads as a timer, so no two waits agree.
    assert len(waits) == 40


def test_a_window_that_just_opened_waits_exactly_as_long_as_one_that_has_not():
    # The first wait is the same wait: nothing about opening Kitty is special.
    bee = settled()
    first = bee.wait
    assert bee.idle == 0.0
    run(bee, first - 0.5)
    assert not bee.visible
    run(bee, 1.0)
    assert bee.visible


def test_moving_the_caret_starts_the_wait_over_with_a_new_length():
    bee = settled()
    run(bee, bee.wait - 0.5)
    before = bee.wait
    bee.move_to(CursorCell(9, 20))
    assert bee.idle == 0.0
    assert bee.wait != before
    # The clock restarted, so the half-second that was left buys nothing.
    run(bee, 1.0)
    assert not bee.visible


def test_the_wait_starts_over_when_the_bee_leaves():
    bee = settled()
    run(bee, bee.wait + 0.1)
    assert bee.visible
    while bee.visible:
        bee.update(1 / 60, HEAD)
    assert bee.idle <= 1 / 60
    assert bee.wait >= bee.interval * INTERVAL_JITTER[0]


def test_how_often_a_bee_comes_does_not_change_the_visit_it_makes():
    def visit(interval: float) -> tuple:
        bee = settled(interval=interval, seed=11)
        run(bee, bee.wait + 0.1)
        assert bee.visible
        return (
            bee._source,
            bee._bend,
            bee._hover_offset,
            bee.landing_offset,
            bee.approach_time,
            bee.hover_time,
            bee.rest_time,
        )

    # The wait draws from a stream of its own, so a rarer bee is the same bee.
    assert visit(20.0) == visit(45.0)


def test_a_disabled_bee_never_arrives():
    bee = NoBee(random.Random(3), interval=0.0)
    bee.set_geometry(VIEWPORT, (50, 45))
    bee.move_to(CursorCell(8, 12))
    run(bee, 10.0)
    assert not bee.visible


def test_the_interval_comes_from_the_flag_the_environment_or_the_default(monkeypatch):
    monkeypatch.delenv(BEE_INTERVAL_VARIABLE, raising=False)
    assert bee_interval(None) == BEE_INTERVAL_DEFAULT == DEFAULT_INTERVAL
    assert bee_interval(30.0) == 30.0
    # Zero seconds is a real setting - a bee as soon as the caret stops.
    assert bee_interval(0.0) == 0.0
    monkeypatch.setenv(BEE_INTERVAL_VARIABLE, " 45 ")
    assert bee_interval(None) == 45.0
    # An explicit flag still wins over the configured default.
    assert bee_interval(12.0) == 12.0


@pytest.mark.parametrize("value", ["soon", "  "])
def test_an_unreadable_interval_keeps_the_default(monkeypatch, capsys, value):
    monkeypatch.setenv(BEE_INTERVAL_VARIABLE, value)
    assert bee_interval(None) == BEE_INTERVAL_DEFAULT
    if value.strip():
        assert BEE_INTERVAL_VARIABLE in capsys.readouterr().err


@pytest.mark.parametrize("value", [-1.0, BEE_INTERVAL_LIMIT + 1, math.inf, math.nan])
def test_an_interval_outside_the_range_keeps_the_default(monkeypatch, capsys, value):
    monkeypatch.delenv(BEE_INTERVAL_VARIABLE, raising=False)
    assert bee_interval(value) == BEE_INTERVAL_DEFAULT
    assert "outside" in capsys.readouterr().err


def test_the_bee_is_on_unless_the_flag_or_the_environment_says_otherwise(monkeypatch):
    monkeypatch.delenv(BEE_VARIABLE, raising=False)
    assert bee_enabled(False) is True
    assert bee_enabled(True) is False
    for word in ("0", "off", "no", "false", "none", "never", " OFF "):
        monkeypatch.setenv(BEE_VARIABLE, word)
        assert bee_enabled(False) is False
    # A variable set for some other purpose leaves the flower its visitor.
    for word in ("1", "yes", "please", ""):
        monkeypatch.setenv(BEE_VARIABLE, word)
        assert bee_enabled(False) is True


def test_the_flags_default_to_a_bee_on_the_usual_schedule():
    assert build_parser().parse_args([]).bee_every is None
    assert build_parser().parse_args([]).no_bee is False
    assert build_parser().parse_args(["--bee-every", "45"]).bee_every == 45.0
    assert build_parser().parse_args(["--no-bee"]).no_bee is True
