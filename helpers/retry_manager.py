# helpers/retry_manager.py
from datetime import datetime, timedelta, timezone
import pytz
import os
import requests
import traceback

from config import supabase, BOLNA_TOKEN, BITRIX_WEBHOOK

IST = pytz.timezone("Asia/Kolkata")
MAX_ATTEMPTS_DEFAULT = 10
RETRY_INTERVAL_HOURS = 2
CALL_CUTOFF_HOUR = 6  # 6 PM IST

# ----------------- Supabase helpers -----------------

def insert_or_increment_retry(lead_id: str, phone: str, lead_name: str = None, reason: str = None):
    """
    If an entry exists (paused=False and attempts < max), increment attempts and update next_call_at.
    Else insert new entry with next_call_at = now + RETRY_INTERVAL_HOURS (or immediate if you want).
    """
    try:
        # Check existing active entry
        q = supabase.table("outbound_call_retries").select("*").eq("lead_id", lead_id).execute()
        rows = q.data or []
        now = datetime.now(timezone.utc)
        if rows:
            row = rows[0]
            if row.get("paused"):
                return row
            attempts = (row.get("attempts") or 0) + 1
            max_attempts = row.get("max_attempts") or MAX_ATTEMPTS_DEFAULT
            next_call = compute_next_call_time(attempts, row.get("lead_name"))
            updated = supabase.table("outbound_call_retries").update({
                "attempts": attempts,
                "next_call_at": next_call.isoformat(),
                "last_status": reason,
                "updated_at": now.isoformat(),
                "bolna_call_ids": row.get("bolna_call_ids") or []
            }).eq("lead_id", lead_id).execute()
            return updated.data[0] if updated.data else None
        else:
            # new entry
            next_call = compute_next_call_time(0,row.get("lead_name"))
            payload = {
                "lead_id": lead_id,
                "lead_name": lead_name,
                "phone": phone,
                "attempts": 0,
                "max_attempts": MAX_ATTEMPTS_DEFAULT,
                "next_call_at": next_call.isoformat(),
                "last_status": reason,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "paused": False,
                "bolna_call_ids": []
            }
            res = supabase.table("outbound_call_retries").insert(payload).execute()
            return res.data[0] if res.data else None
    except Exception as e:
        print("❌ insert_or_increment_retry error:", e, traceback.format_exc())
        return None

def compute_next_call_time(attempts: int, lead_first_name: str | None):
    now_ist = datetime.now(IST)
    policy = get_lead_calling_policy(lead_first_name)

    if policy["retry_interval_unit"] == "minutes":
        candidate = now_ist + timedelta(minutes=policy["retry_interval_minutes"])
    else:
        candidate = now_ist + timedelta(hours=policy["retry_interval_hours"])

    return candidate.astimezone(timezone.utc)


def mark_retry_attempt(lead_id: str, bolna_call_id: str = None, status: str = None):
    """Increment attempts and optionally append bolna_call_id, update last_status and next_call_at."""
    try:
        q = supabase.table("outbound_call_retries").select("*").eq("lead_id", lead_id).execute()
        rows = q.data or []
        now = datetime.now(timezone.utc)
        if not rows:
            # fallback — create
            return insert_or_increment_retry(lead_id, phone="unknown", lead_name=None, reason=status)
        row = rows[0]
        attempts = (row.get("attempts") or 0) + 1
        max_attempts = row.get("max_attempts") or MAX_ATTEMPTS_DEFAULT
        bolna_ids = row.get("bolna_call_ids") or []
        if bolna_call_id:
            bolna_ids = bolna_ids + [bolna_call_id]
        next_call = compute_next_call_time(attempts, row.get("lead_name"))
        payload = {
            "attempts": attempts,
            "last_status": status,
            "bolna_call_ids": bolna_ids,
            "updated_at": now.isoformat(),
            "next_call_at": next_call.isoformat()
        }
        # If attempts exceed max, mark paused True and leave reason
        if attempts >= max_attempts:
            payload["paused"] = True
            payload["last_status"] = "max_attempts_reached"
        res = supabase.table("outbound_call_retries").update(payload).eq("lead_id", lead_id).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        print("❌ mark_retry_attempt error:", e)
        return None

def cancel_retry_for_lead(lead_id: str, reason: str = "cleared"):
    """Pause or delete retry entries for this lead (when call picked up or manual action)."""
    try:
        now = datetime.now(timezone.utc)
        res = supabase.table("outbound_call_retries").update({
            "paused": True,
            "last_status": reason,
            "updated_at": now.isoformat()
        }).eq("lead_id", lead_id).execute()
        return res.data
    except Exception as e:
        print("❌ cancel_retry_for_lead error:", e)
        return None

# Query due entries
def get_due_retries(limit=200):
    now = datetime.now(timezone.utc).isoformat()
    try:
        q = (supabase.table("outbound_call_retries")
             .select("*")
             .lte("next_call_at", now)
             .eq("paused", False)
             .execute())
        return q.data or []
    except Exception as e:
        print("❌ get_due_retries error:", e)
        return []

# ----------------- Bolna caller -----------------

def get_lead_calling_policy(lead_first_name: str | None):
    """
    Returns retry interval and cutoff hour based on lead.
    """
    if lead_first_name and lead_first_name.lower() == "udipth":
        return {
            "retry_interval_minutes": RETRY_INTERVAL_HOURS * 60,  # minutes
            "retry_interval_unit": "minutes",
            "call_cutoff_hour": 23
        }
    else:
        return {
            "retry_interval_hours": RETRY_INTERVAL_HOURS,
            "retry_interval_unit": "hours",
            "call_cutoff_hour": CALL_CUTOFF_HOUR
        }


def place_bolna_call(phone: str, lead_id: str, lead_name: str = None, user_data: dict = None,lead_first_name: str = None):
    """
    Trigger Bolna call API and return bolna response JSON.
    Ensure BOLNA_TOKEN env var is set.
    """
    try:
        if not BOLNA_TOKEN:
            raise RuntimeError("BOLNA_TOKEN not set")
            # Select agent based on lead_name
        if "udipth" in lead_name.lower() or "udipth" in lead_first_name.lower():
            agent_id = "f11a2955-9639-42bb-b77f-d198f5dc352b"
        else:
            agent_id = "c363b7ee-0225-47f1-86b4-86c91dfabeb8"
            
        payload = {
            "agent_id": agent_id,
            "recipient_phone_number": phone,
            "from_phone_number": os.getenv("CALLER_ID", "+918035316588"),
            "user_data": {"lead_id": lead_id, "lead_name": lead_name}
        }
        if user_data:
            payload["user_data"].update(user_data)
        headers = {
            "Authorization": f"Bearer {BOLNA_TOKEN}",
            "Content-Type": "application/json"
        }
        resp = requests.post("https://api.bolna.ai/call", json=payload, headers=headers, timeout=20)
        return resp.json()
    except Exception as e:
        print("❌ place_bolna_call error:", e)
        return {"error": str(e)}

# ----------------- Scheduling logic (cutoff handling) -----------------

def is_sunday_blackout_window(now_ist: datetime) -> bool:
    """
    Returns True if current time is Sunday between 10:00 and 12:00 IST
    """
    # weekday(): Monday=0 ... Sunday=6
    if now_ist.weekday() == 6:
        if 10 <= now_ist.hour < 12:
            return True
    return False


def can_place_call_now(lead_created_at_str: str | None, attempts: int):
    """
    Rule:
      - No calls allowed after 18:00 IST, except allow 1st call if lead was created after 18:00 (special case).
      - attempts is current attempts count (0 for first).
    """
    now_ist = datetime.now(IST)
    policy = get_lead_calling_policy(lead_first_name)

    cutoff_hour = policy["call_cutoff_hour"]

    # 🔴 SUNDAY BLACKOUT WINDOW
    if is_sunday_blackout_window(now_ist):
        return False



    hour = now_ist.hour

    if hour < cutoff_hour:
        return True

    # if past cutoff and it's the first attempt, allow the first attempt only if lead created after cutoff
    if attempts == 0 and lead_created_at_str:
        try:
            # Bitrix date strings vary; attempt to parse ISO-like
            from dateutil import parser
            lead_created = parser.parse(lead_created_at_str).astimezone(IST)
            if lead_created.hour >= cutoff_hour:
                return True
        except Exception:
            # If we cannot parse, be conservative -> disallow
            return False
    return False

# ----------------- Process due retries -----------------

def process_due_retries(verify_bitrix_lead=True, limit=200):
    """
    Process entries whose next_call_at <= now.
    Returns a list of dicts describing actions.
    """
    results = []
    due = get_due_retries(limit=limit)
    for r in due:
        lead_id = r.get("lead_id")
        phone = r.get("phone")
        attempts = r.get("attempts") or 0
        max_attempts = r.get("max_attempts") or MAX_ATTEMPTS_DEFAULT

        if lead_id:
            get_res = requests.get(
            f"{BITRIX_WEBHOOK}crm.lead.get.json",
            params={"id": lead_id},
        )
        lead_data = get_res.json().get("result", {})
        existing_comments = lead_data.get("COMMENTS") or ""

        requests.post(
            f"{BITRIX_WEBHOOK}crm.lead.update.json",
            json={
                "id": lead_id,
                "fields": {
                    "COMMENTS": existing_comments + 
                        f"<p><b>Retry Attempt:</b> User did not pick up. Email sent.</p>"
                }
            }
        )

        # Fetch lead created_at for cutoff logic
        lead_created_at = None
        try:
            br = requests.get(f"{BITRIX_WEBHOOK}crm.lead.get.json", params={"id": lead_id}, timeout=10)
            lead_data = br.json().get("result", {})
            lead_created_at = lead_data.get("DATE_CREATE") or lead_data.get("created_at")
        except Exception as e:
            print("⚠️ could not fetch lead created_at:", e)

        if attempts >= max_attempts:
            # mark paused
            cancel_retry_for_lead(lead_id, reason="max_attempts_reached")
            results.append({"lead_id": lead_id, "action": "paused_max_attempts"})
            continue

        lead_first_name = lead_data.get("NAME")

        if not can_place_call_now(lead_created_at, attempts, lead_first_name):
            # reschedule for next working window (tomorrow 10am IST maybe)
            policy = get_lead_calling_policy(lead_first_name)
            now_ist = datetime.now(IST)


            
            # If blocked due to Sunday 10–12 window → move to 12:01 PM
            if is_sunday_blackout_window(now_ist):
                next_try = now_ist.replace(hour=12, minute=1, second=0, microsecond=0)
            else:
                if policy["retry_interval_unit"] == "minutes":
                    next_try = now_ist + timedelta(minutes=policy["retry_interval_minutes"])
                else:
                    next_try = now_ist + timedelta(hours=policy["retry_interval_hours"])


            supabase.table("outbound_call_retries").update({
                "next_call_at": next_try.isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat()
            }).eq("lead_id", lead_id).execute()
            results.append({"lead_id": lead_id, "action": "rescheduled_due_to_cutoff"})
            continue

        lead_first_name = lead_data.get("NAME")

        # Place call
        bolna_response = place_bolna_call(phone=phone, lead_id=lead_id, lead_name=r.get("lead_name"),lead_first_name=lead_first_name)
        bolna_id = bolna_response.get("id") or bolna_response.get("call_id") or None

        # Mark attempt and update bolna id
        mark_retry_attempt(lead_id=lead_id, bolna_call_id=bolna_id, status="scheduled")

        results.append({"lead_id": lead_id, "phone": phone, "bolna_id": bolna_id, "action": "call_scheduled"})
    return results
