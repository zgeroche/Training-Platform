from datetime import datetime

from app.timefmt import fmt_local


def test_mountain_time_winter_and_summer():
    # Stored times are naive UTC. Winter is MST (UTC-7), summer is MDT (UTC-6).
    assert fmt_local(datetime(2026, 11, 18, 3, 49)) == "Tue, Nov 17, 8:49 PM MST"
    assert fmt_local(datetime(2026, 7, 1, 18, 0)) == "Wed, Jul 1, 12:00 PM MDT"
    assert fmt_local(datetime(2026, 11, 17, 19, 23), date=False) == "12:23 PM MST"
    assert fmt_local(None) == "never"
