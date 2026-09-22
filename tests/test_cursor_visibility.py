from flower_cursor.cursor_visibility import HANDOVER_DELAY, NativeCursorPolicy


def test_a_caret_hidden_for_a_redraw_keeps_the_flower() -> None:
    policy = NativeCursorPolicy()

    assert policy.child_set(False, 10.0) is False
    assert policy.poll(10.0 + HANDOVER_DELAY / 2) is False
    assert policy.child_set(True, 10.0 + HANDOVER_DELAY / 2) is False
    assert policy.poll(100.0) is False
    assert policy.child_draws_caret is False


def test_a_caret_that_stays_hidden_hands_the_flower_over() -> None:
    policy = NativeCursorPolicy()
    policy.child_set(False, 10.0)

    assert policy.poll(10.0 + HANDOVER_DELAY) is True
    assert policy.child_draws_caret is True
    assert policy.poll(11.0) is False, "the handover is reported once, not every tick"


def test_the_caret_coming_back_takes_it_over_again_at_once() -> None:
    policy = NativeCursorPolicy()
    policy.child_set(False, 10.0)
    policy.poll(10.0 + HANDOVER_DELAY)

    assert policy.child_set(True, 20.0) is True
    assert policy.child_draws_caret is False
    assert policy.child_set(True, 21.0) is False, "a caret already ours is not a change"


def test_repeated_hide_requests_measure_from_the_first() -> None:
    policy = NativeCursorPolicy()
    policy.child_set(False, 10.0)
    policy.child_set(False, 10.0 + HANDOVER_DELAY / 2)

    assert policy.poll(10.0 + HANDOVER_DELAY) is True
