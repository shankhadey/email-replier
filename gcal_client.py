"""
Google Calendar: fetch free/busy slots for the next N days.
Only called when classifier detects a meeting scheduling request.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from auth import get_calendar_service

logger = logging.getLogger(__name__)


def get_free_slots(
    days_ahead: int = 7,
    work_start: int = 8,
    work_end: int = 18,
    tz_name: str = "America/Chicago",
) -> str:
    """
    Returns a human-readable string of free slots for the next N days
    during working hours, formatted naturally for email insertion.
    tz_name: IANA timezone for the user (default: America/Chicago for Shankha).
    """
    import zoneinfo
    service = get_calendar_service()
    user_tz = zoneinfo.ZoneInfo(tz_name)
    now = datetime.now(user_tz)
    time_min = now.isoformat()
    time_max = (now + timedelta(days=days_ahead)).isoformat()

    try:
        result = service.freebusy().query(body={
            "timeMin": time_min,
            "timeMax": time_max,
            "items": [{"id": "primary"}],
        }).execute()

        busy_periods = result["calendars"]["primary"]["busy"]
        free_slots = _compute_free_slots(now, days_ahead, busy_periods, work_start, work_end)
        result_str = _format_free_slots(free_slots)
        logger.info(f"Free slots computed: {repr(result_str)}")
        return result_str

    except Exception as e:
        logger.error(f"Error fetching calendar: {e}")
        return ""


def _compute_free_slots(
    now: datetime,
    days_ahead: int,
    busy_periods: list[dict],
    work_start: int,
    work_end: int,
) -> list[dict]:
    """Compute free windows within working hours given busy periods."""
    free = []
    local_now = now  # now is already in user_tz

    for day_offset in range(days_ahead):
        day = (local_now + timedelta(days=day_offset)).date()
        day_start = datetime(day.year, day.month, day.day, work_start, 0,
                             tzinfo=local_now.tzinfo)
        day_end = datetime(day.year, day.month, day.day, work_end, 0,
                           tzinfo=local_now.tzinfo)

        if day_end <= local_now:
            continue

        if day_start < local_now:
            day_start = local_now

        # Build busy windows for this day
        day_busy = []
        for b in busy_periods:
            b_start = datetime.fromisoformat(b["start"].replace("Z", "+00:00")).astimezone(local_now.tzinfo)
            b_end = datetime.fromisoformat(b["end"].replace("Z", "+00:00")).astimezone(local_now.tzinfo)
            if b_start.date() <= day <= b_end.date():
                day_busy.append((b_start, b_end))
        day_busy.sort(key=lambda x: x[0])

        # Walk through the day finding free windows >= 30 minutes
        cursor = day_start
        for b_start, b_end in day_busy:
            if cursor < b_start:
                slot_duration = (b_start - cursor).total_seconds() / 60
                if slot_duration >= 30:
                    free.append({"start": cursor, "end": b_start})
            cursor = max(cursor, b_end)

        if cursor < day_end:
            slot_duration = (day_end - cursor).total_seconds() / 60
            if slot_duration >= 30:
                free.append({"start": cursor, "end": day_end})

    return free


def _format_free_slots(slots: list[dict]) -> str:
    """Format free slots in Shankha's voice style (e.g., 2/18: 12-6pm)."""
    if not slots:
        return "I'm fully booked this week."

    lines = []
    by_day: dict = {}
    for slot in slots:
        day_key = slot["start"].strftime("%-m/%-d")
        if day_key not in by_day:
            by_day[day_key] = []
        by_day[day_key].append(slot)

    for day_key, day_slots in by_day.items():
        parts = []
        for s in day_slots:
            start_fmt = _fmt_time(s["start"])
            end_fmt = _fmt_time(s["end"])
            parts.append(f"{start_fmt}-{end_fmt}")
        lines.append(f"{day_key}: {', '.join(parts)}")

    return "\n".join(lines)


def _fmt_time(dt: datetime) -> str:
    """Format like 12pm, 10:30am."""
    h = dt.hour
    m = dt.minute
    period = "am" if h < 12 else "pm"
    h12 = h % 12 or 12
    if m == 0:
        return f"{h12}{period}"
    return f"{h12}:{m:02d}{period}"
