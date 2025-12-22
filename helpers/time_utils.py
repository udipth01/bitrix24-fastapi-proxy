# helpers/time_utils.py

from datetime import datetime, timedelta
import pytz  # install with: pip install pytz
import re
from dateutil.parser import isoparse


def utc_to_ist(utc_timestamp: str, fmt="%Y-%m-%dT%H:%M:%S%z"):
    """
    Convert a UTC timestamp string to IST timezone.
    :param utc_timestamp: UTC timestamp string (e.g., '2025-08-19T09:30:00+0000')
    :param fmt: Input format
    :return: IST datetime string
    """
    utc_zone = pytz.timezone("UTC")
    ist_zone = pytz.timezone("Asia/Kolkata")

    utc_dt = datetime.strptime(utc_timestamp, fmt)
    utc_dt = utc_zone.localize(utc_dt)
    ist_dt = utc_dt.astimezone(ist_zone)

    return ist_dt.strftime("%Y-%m-%d %H:%M:%S")

def parse_rm_meeting_time(rm_str: str | None):
    """
    Returns:
      - start_dt: 'YYYY-MM-DDTHH:MM:SS'
      - date_only: 'YYYY-MM-DD'

    Supports ALL common formats:
      - "tomorrow 18:00"
      - "2025-11-27 15:00"
      - "27-11-2025 15:00"
      - "27/11/2025 20:00"
      - "15:00 27/11/2025"
      - "15:00 27-11-2025"
    """
    if not rm_str:
        return None, None

    s = rm_str.lower().strip()
    ist_zone = pytz.timezone("Asia/Kolkata")
    now_ist = datetime.now(ist_zone)

    # -------- 1️⃣ CASE: "tomorrow 18:00" --------
    m = re.search(r"(\d{1,2}):(\d{2})", s)
    if "tomorrow" in s and m:
        hour = int(m.group(1))
        minute = int(m.group(2))
        dt = (now_ist + timedelta(days=1)).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        return dt.strftime("%Y-%m-%dT%H:%M:%S"), dt.strftime("%Y-%m-%d")

    # -------- 2️⃣ CASE: "DD/MM/YYYY HH:MM" or "DD-MM-YYYY HH:MM" --------
    m = re.match(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})\s+(\d{1,2}):(\d{2})$", s)
    if m:
        dd, mm, yyyy = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hour, minute = int(m.group(4)), int(m.group(5))
        dt = ist_zone.localize(datetime(yyyy, mm, dd, hour, minute))
        return dt.strftime("%Y-%m-%dT%H:%M:%S"), dt.strftime("%Y-%m-%d")

    # -------- 3️⃣ CASE: "HH:MM DD/MM/YYYY" or "HH:MM DD-MM-YYYY" --------
    m = re.match(r"(\d{1,2}):(\d{2})\s+(\d{1,2})[/-](\d{1,2})[/-](\d{4})$", s)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        dd, mm, yyyy = int(m.group(3)), int(m.group(4)), int(m.group(5))
        dt = ist_zone.localize(datetime(yyyy, mm, dd, hour, minute))
        return dt.strftime("%Y-%m-%dT%H:%M:%S"), dt.strftime("%Y-%m-%d")

    # -------- 4️⃣ CASE: Standard formats --------
    for fmt in ["%Y-%m-%d %H:%M", "%d-%m-%Y %H:%M"]:
        try:
            dt = datetime.strptime(rm_str, fmt)
            dt = ist_zone.localize(dt)
            return dt.strftime("%Y-%m-%dT%H:%M:%S"), dt.strftime("%Y-%m-%d")
        except:
            pass

    print("⚠️ Could not parse RM_meeting_time:", rm_str)
    return None, None


IST = pytz.timezone("Asia/Kolkata")

def compute_busy_call_datetime(busy_call_next: dict) -> datetime | None:
    """
    Converts AI busy_call_next semantic payload into absolute IST datetime.
    Returns None if unusable.
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
        # ABSOLUTE / AFTER DATE
        # -------------------------
        elif callback_type in ("absolute_date", "after_date"):
            date_str = busy_call_next.get("absolute_date")
            if not date_str:
                return None
            dt = isoparse(date_str)
            if dt.tzinfo is None:
                dt = IST.localize(dt)

        # -------------------------
        # UNSPECIFIED
        # -------------------------
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

    except Exception:
        return None
