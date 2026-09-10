from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .report_generation import ReportingPeriod


def utc_instant(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("An explicit timezone-aware instant is required.")
    return value.astimezone(timezone.utc)


def iana_timezone(name: str) -> ZoneInfo:
    try:
        if name in {"localtime", "posixrules"} or name.startswith(("posix/", "right/")):
            raise ValueError("Server-local and implementation-specific timezone aliases are not allowed.")
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
        raise ValueError("A valid IANA timezone is required.") from exc


@dataclass(frozen=True)
class Recurrence:
    frequency: str
    local_time: time
    timezone: str
    period_policy: str
    day_of_week: int | None = None
    day_of_month: int | None = None

    def __post_init__(self) -> None:
        iana_timezone(self.timezone)
        if self.local_time.tzinfo is not None or self.local_time.second or self.local_time.microsecond:
            raise ValueError("local_time must have minute precision without a UTC offset.")
        if self.frequency == "WEEKLY":
            # ISO calendar ordering, represented as Python weekday(): Monday=0, Sunday=6.
            if self.day_of_week is None or not 0 <= self.day_of_week <= 6 or self.day_of_month is not None or self.period_policy != "previous_week":
                raise ValueError("Weekly schedules require day_of_week 0..6 and previous_week only.")
        elif self.frequency == "MONTHLY":
            if self.day_of_month is None or not 1 <= self.day_of_month <= 31 or self.day_of_week is not None or self.period_policy != "previous_month":
                raise ValueError("Monthly schedules require day_of_month 1..31 and previous_month only.")
        else:
            raise ValueError("Only WEEKLY and MONTHLY are supported.")


def _local_instants(local: datetime, zone: ZoneInfo) -> list[datetime]:
    candidates = {local.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc) for fold in (0, 1)}
    return sorted(instant for instant in candidates if instant.astimezone(zone).replace(tzinfo=None) == local)


def resolve_local_instant(local: datetime, zone: ZoneInfo) -> datetime:
    """Fold: earlier UTC instant, once only. Gap: first valid local instant after the gap.

    Round trips through zoneinfo distinguish real wall times from fabricated offsets.
    Search at second precision also handles historical non-hour transitions and skipped dates.
    """
    if local.tzinfo is not None:
        raise ValueError("Expected a naive local wall time.")
    for seconds in range(172801):
        candidates = _local_instants(local + timedelta(seconds=seconds), zone)
        if candidates:
            return candidates[0]
    raise ValueError("Could not resolve a local time within two days.")


def next_occurrence(recurrence: Recurrence, *, after: datetime) -> datetime:
    after = utc_instant(after)
    zone = iana_timezone(recurrence.timezone)
    local_date = after.astimezone(zone).date()
    if recurrence.frequency == "WEEKLY":
        candidate = local_date + timedelta(days=(recurrence.day_of_week - local_date.weekday()) % 7)
        while True:
            instant = resolve_local_instant(datetime.combine(candidate, recurrence.local_time), zone)
            if instant > after:
                return instant
            candidate += timedelta(days=7)
    year, month = local_date.year, local_date.month
    while True:
        day = min(recurrence.day_of_month, monthrange(year, month)[1])
        instant = resolve_local_instant(datetime.combine(date(year, month, day), recurrence.local_time), zone)
        if instant > after:
            return instant
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


@dataclass(frozen=True)
class CompletedReportingPeriod:
    start: datetime
    end: datetime  # Exclusive UTC end; do not subtract microseconds for datasource queries.
    start_date: date
    end_date: date  # Inclusive local date, matching canonical ReportingPeriod.

    def canonical(self) -> ReportingPeriod:
        return ReportingPeriod("custom", self.start_date.isoformat(), self.end_date.isoformat())


def reporting_period(recurrence: Recurrence, *, scheduled_for: datetime) -> CompletedReportingPeriod:
    zone = iana_timezone(recurrence.timezone)
    local_date = utc_instant(scheduled_for).astimezone(zone).date()
    if recurrence.period_policy == "previous_week":
        end_date = local_date - timedelta(days=local_date.weekday())
        start_date = end_date - timedelta(days=7)
    else:
        end_date = local_date.replace(day=1)
        start_date = (end_date - timedelta(days=1)).replace(day=1)
    return CompletedReportingPeriod(
        resolve_local_instant(datetime.combine(start_date, time()), zone),
        resolve_local_instant(datetime.combine(end_date, time()), zone),
        start_date, end_date - timedelta(days=1),
    )


def occurrence_identity(schedule_id: int, scheduled_for: datetime) -> str:
    # Revision is deliberately excluded: an edit must not duplicate the same occurrence.
    instant = utc_instant(scheduled_for).isoformat(timespec="microseconds")
    return f"scheduled-report:{schedule_id}:{instant}"
