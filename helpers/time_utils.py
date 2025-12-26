# helpers/time_utils.py

from datetime import datetime, timedelta
import pytz
from dateutil.parser import parse, isoparse
import re

IST = pytz.timezone("Asia/Kolkata")


# --------------------------------------------------
# UTC → IST
# --------------------------------------------------
def utc_to_ist(utc_timestamp: str, fmt="%Y-%m-%dT%H:%M:%S%z"):
    utc_zone = pytz.timezone("UTC")
    ist_zone = IST

    utc_dt = datetime.strptime(utc_timestamp, fmt)
    utc_dt = utc_zone.localize(utc_dt)
    ist_dt = utc_dt.astimezone(ist_zone)

    return ist_dt.strftime("%Y-%m-%d %H:%M:%S")


# --------------------------------------------------
# RM MEETING TIME (LLM SAFE)
# --------------------------------------------------
def parse_rm_meeting_time(rm_str: str | None):
    """
    Returns:
      - start_dt: 'YYYY-MM-DDTHH:MM:SS'
      - date_only: 'YYYY-MM-DD'

    Handles:
      - 27/12/2025 01:00 PM
      - 27-12-2025 13:00
      - 2025-12-27 15:00
      - tomorrow 6 pm
      - today 10:30
    """
    if not rm_str:
        return None, None

    s = rm_str.strip().lower()
    now = datetime.now(IST)

    try:
        # ------------------------------------------
        # HANDLE RELATIVE WORDS
        # ------------------------------------------
        if "tomorrow" in s or "today" in s:
            base_date = now.date()
            if "tomorrow" in s:
                base_date += timedelta(days=1)

            time_match = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", s)
            if time_match:
                hour = int(time_match.group(1))
                minute = int(time_match.group(2) or 0)
                ampm = time_match.group(3)

                if ampm:
                    if ampm == "pm" and hour != 12:
                        hour += 12
                    if ampm == "am" and hour == 12:
                        hour = 0

                dt = IST.localize(
                    datetime(
                        base_date.year,
                        base_date.month,
                        base_date.day,
                        hour,
                        minute,
                    )
                )
            else:
                dt = IST.localize(datetime.combine(base_date, datetime.min.time()))

        # ------------------------------------------
        # ABSOLUTE DATES (AM/PM, ANY FORMAT)
        # ------------------------------------------
        else:
            dt = parse(rm_str, dayfirst=True)

            if dt.tzinfo is None:
                dt = IST.localize(dt)
            else:
                dt = dt.astimezone(IST)

        return (
            dt.strftime("%Y-%m-%dT%H:%M:%S"),
            dt.strftime("%Y-%m-%d"),
        )

    except Exception as e:
        print("⚠️ Could not parse RM_meeting_time:", rm_str, "|", e)
        return None, None


# --------------------------------------------------
# BUSY CALL NEXT (SEMANTIC PAYLOAD)
# --------------------------------------------------
def compute_busy_call_datetime(busy_call_next: dict) -> datetime | None:
    """
    Converts AI busy_call_next semantic payload into absolute IST datetime.
    """
    if not busy_call_next:
        return None

    callback_type = busy_call_next.get("callback_type")
    now = datetime.now(IST)

    try:
        # -------------------------
        # RELATIVE DAY
        # -------------------------
        if callback_type == "relative_day":
            days = int(busy_call_next.get("day_offset") or 0)
            dt = now + timedelta(days=days)

        # -------------------------
        # RELATIVE TIME
        # -------------------------
        elif callback_type == "relative_time":
            hours = int(busy_call_next.get("hour_offset") or 0)
            minutes = int(busy_call_next.get("minute_offset") or 0)
            dt = now + timedelta(hours=hours, minutes=minutes)

        # -------------------------
        # ABSOLUTE DATE
        # -------------------------
        elif callback_type in ("absolute_date", "after_date"):
            date_str = busy_call_next.get("absolute_date")
            if not date_str:
                return None

            dt = isoparse(date_str)
            if dt.tzinfo is None:
                dt = IST.localize(dt)
            else:
                dt = dt.astimezone(IST)

        else:
            return None

        # -------------------------
        # OPTIONAL TIME OVERRIDE
        # -------------------------
        time_str = busy_call_next.get("time")
        if time_str:
            hh, mm = map(int, time_str.split(":"))
            dt = dt.replace(hour=hh, minute=mm, second=0, microsecond=0)

        return dt

    except Exception as e:
        print("⚠️ Failed to compute busy_call_next:", busy_call_next, "|", e)
        return None
