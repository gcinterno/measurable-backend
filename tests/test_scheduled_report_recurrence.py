from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.scheduled_report_recurrence import Recurrence, next_occurrence, occurrence_identity, reporting_period


def weekly(zone="America/Mexico_City", *, day=0, hour=8, minute=0):
    return Recurrence("WEEKLY", time(hour, minute), zone, "previous_week", day_of_week=day)


def monthly(day=1, zone="America/Mexico_City", *, hour=8):
    return Recurrence("MONTHLY", time(hour), zone, "previous_month", day_of_month=day)


def test_weekly_monday_zero_and_strictly_future():
    recurrence = weekly()
    occurrence = datetime(2026, 9, 14, 14, tzinfo=timezone.utc)
    assert next_occurrence(recurrence, after=datetime(2026, 9, 9, tzinfo=timezone.utc)) == occurrence
    assert next_occurrence(recurrence, after=occurrence) == occurrence + timedelta(days=7)


def test_monthly_mexico_city():
    assert next_occurrence(monthly(), after=datetime(2026, 9, 9, tzinfo=timezone.utc)) == datetime(2026, 10, 1, 14, tzinfo=timezone.utc)


@pytest.mark.parametrize("year,month,day,expected", [
    (2027, 2, 29, 28), (2027, 2, 30, 28), (2027, 2, 31, 28),
    (2028, 2, 29, 29), (2028, 2, 30, 29), (2028, 2, 31, 29),
    (2026, 4, 29, 29), (2026, 4, 30, 30), (2026, 4, 31, 30),
    (2100, 2, 31, 28), (2000, 2, 31, 29), (2026, 12, 31, 31),
])
def test_month_end_clamping(year, month, day, expected):
    occurrence = next_occurrence(monthly(day, "UTC"), after=datetime(year, month, 1, tzinfo=timezone.utc))
    assert occurrence == datetime(year, month, expected, 8, tzinfo=timezone.utc)


def test_clamping_does_not_mutate_requested_day_or_skip_following_month():
    recurrence = monthly(31, "UTC")
    february = next_occurrence(recurrence, after=datetime(2027, 2, 1, tzinfo=timezone.utc))
    assert next_occurrence(recurrence, after=february) == datetime(2027, 3, 31, 8, tzinfo=timezone.utc)


def test_dst_gap_moves_to_first_valid_instant_not_wall_time_plus_gap():
    recurrence = weekly("America/New_York", day=6, hour=2, minute=30)
    result = next_occurrence(recurrence, after=datetime(2026, 3, 8, tzinfo=timezone.utc))
    assert result == datetime(2026, 3, 8, 7, tzinfo=timezone.utc)
    assert result.astimezone(ZoneInfo(recurrence.timezone)).hour == 3
    assert result.astimezone(ZoneInfo(recurrence.timezone)).minute == 0


def test_dst_fold_chooses_earlier_instant_only_once():
    recurrence = weekly("America/New_York", day=6, hour=1, minute=30)
    first = next_occurrence(recurrence, after=datetime(2026, 11, 1, tzinfo=timezone.utc))
    assert first == datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc)
    assert next_occurrence(recurrence, after=first + timedelta(minutes=10)) == datetime(2026, 11, 8, 6, 30, tzinfo=timezone.utc)


def test_half_hour_dst_gap():
    recurrence = weekly("Australia/Lord_Howe", day=6, hour=2, minute=15)
    result = next_occurrence(recurrence, after=datetime(2026, 10, 3, tzinfo=timezone.utc))
    local = result.astimezone(ZoneInfo(recurrence.timezone))
    assert (local.date(), local.time()) == (date(2026, 10, 4), time(2, 30))


def test_skipped_calendar_date_moves_forward():
    recurrence = weekly("Pacific/Apia", day=4, hour=8)
    result = next_occurrence(recurrence, after=datetime(2011, 12, 29, 20, tzinfo=timezone.utc))
    assert result.astimezone(ZoneInfo(recurrence.timezone)).replace(tzinfo=None) == datetime(2011, 12, 31)


def test_previous_week_uses_completed_calendar_week_and_timezone():
    # UTC Monday is still Sunday in Mexico; previous week is Aug 24..30, not Aug 31..Sep 6.
    period = reporting_period(weekly(), scheduled_for=datetime(2026, 9, 7, 1, tzinfo=timezone.utc))
    assert (period.start_date, period.end_date) == (date(2026, 8, 24), date(2026, 8, 30))
    assert period.start == datetime(2026, 8, 24, 6, tzinfo=timezone.utc)
    assert period.end == datetime(2026, 8, 31, 6, tzinfo=timezone.utc)
    assert period.canonical().start_date == "2026-08-24"
    assert period.canonical().end_date == "2026-08-30"


def test_previous_month_uses_local_calendar_not_server_date():
    period = reporting_period(monthly(), scheduled_for=datetime(2026, 10, 1, 14, tzinfo=timezone.utc))
    assert (period.start_date, period.end_date) == (date(2026, 9, 1), date(2026, 9, 30))
    early_utc = reporting_period(monthly(), scheduled_for=datetime(2026, 10, 1, 1, tzinfo=timezone.utc))
    assert early_utc.start_date == date(2026, 8, 1)


def test_previous_week_spanning_dst_is_not_168_hours():
    period = reporting_period(weekly("America/New_York"), scheduled_for=datetime(2026, 3, 9, 12, tzinfo=timezone.utc))
    assert period.end - period.start == timedelta(hours=167)
    assert (period.start_date, period.end_date) == (date(2026, 3, 2), date(2026, 3, 8))


def test_previous_month_year_boundary_and_leap_year():
    assert reporting_period(monthly(), scheduled_for=datetime(2026, 1, 1, 14, tzinfo=timezone.utc)).start_date == date(2025, 12, 1)
    assert reporting_period(monthly(), scheduled_for=datetime(2028, 3, 1, 14, tzinfo=timezone.utc)).end_date == date(2028, 2, 29)


@pytest.mark.parametrize("zone", ["UTC", "America/Mexico_City", "America/New_York", "Europe/London", "Asia/Kathmandu", "Australia/Lord_Howe"])
@pytest.mark.parametrize("year", [2026, 2027, 2028, 2030, 2032])
def test_monthly_occurrences_are_monotonic_and_never_skip_months(zone, year):
    recurrence = monthly(31, zone)
    after = datetime(year, 1, 1, tzinfo=timezone.utc)
    months = []
    for _ in range(12):
        occurrence = next_occurrence(recurrence, after=after)
        assert occurrence > after
        local = occurrence.astimezone(ZoneInfo(zone))
        assert (local + timedelta(days=1)).month != local.month
        months.append(local.month)
        after = occurrence
    assert months == list(range(1, 13))


@pytest.mark.parametrize("updates", [
    {"frequency": "DAILY"}, {"timezone": "Invalid/Zone"}, {"timezone": "../UTC"},
    {"day_of_week": 7}, {"day_of_week": None}, {"day_of_month": 1}, {"period_policy": "previous_month"},
    {"local_time": time(8, 0, 1)}, {"local_time": time(8, tzinfo=timezone.utc)},
    {"timezone": "localtime"}, {"timezone": "posixrules"}, {"timezone": "posix/UTC"}, {"timezone": "right/UTC"},
])
def test_invalid_recurrence_rejected(updates):
    values = {"frequency": "WEEKLY", "local_time": time(8), "timezone": "UTC", "period_policy": "previous_week", "day_of_week": 0}
    with pytest.raises(ValueError):
        Recurrence(**{**values, **updates})


def test_occurrence_identity_is_offset_independent_and_requires_aware_datetime():
    instant = datetime(2026, 10, 1, 14, tzinfo=timezone.utc)
    assert occurrence_identity(3, instant) == occurrence_identity(3, instant.astimezone(ZoneInfo("America/Mexico_City")))
    assert occurrence_identity(3, instant) != occurrence_identity(4, instant)
    with pytest.raises(ValueError):
        occurrence_identity(3, instant.replace(tzinfo=None))
