from app.services.credit_monitor import (
    _AlertState,
    _threshold_to_send,
    estimate_hours_remaining,
)


def test_estimate_hours_remaining():
    assert estimate_hours_remaining(120, 24) == 5
    assert estimate_hours_remaining(10, 0) is None


def test_threshold_selects_only_closest_warning():
    state = _AlertState()
    # At 20 hours left, the 24h warning is the correct single warning.
    assert _threshold_to_send(state, 20) == 24
    state.alerted_thresholds.update({72, 24})
    # Once 24h has fired, do not backfill the 72h warning.
    assert _threshold_to_send(state, 20) is None
