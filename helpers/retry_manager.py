# helpers/retry_manager.py
from datetime import datetime, timedelta, timezone
import pytz
import os
import requests
import traceback

from config import supabase, BOLNA_TOKEN, BITRIX_WEBHOOK
from dateutil.parser import isoparse
from helpers.logger import logger

IST = pytz.timezone("Asia/Kolkata")
MAX_ATTEMPTS_DEFAULT = 10
RETRY_INTERVAL_HOURS = 2
CALL_CUTOFF_HOUR = 6  # 6 PM IST

# ----------------- Supabase helpers -----------------

def insert_or_increment_retry(lead_id: str, phone: str, lead_name: str = None, lead_first_name: str | None = None,reason: str = None):
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
            
            # ✅ do NOT reschedule if already scheduled in future
            if isoparse(row["next_call_at"]) > datetime.now(timezone.utc):
                return row
            # attempts = (row.get("attempts") or 0) + 1
            # max_attempts = row.get("max_attempts") or MAX_ATTEMPTS_DEFAULT
            attempts = row.get("attempts") or 0
            next_call = get_next_allowed_call_time( row.get("lead_first_name"),attempts)
            updated = supabase.table("outbound_call_retries").update({
                # "attempts": attempts,
                "next_call_at": next_call.isoformat(),
                "last_status": reason,
                "updated_at": now.isoformat(),
                "bolna_call_ids": row.get("bolna_call_ids") or []
            }).eq("lead_id", lead_id).execute()
            return updated.data[0] if updated.data else None
        else:
            # new entry
            next_call = get_next_allowed_call_time(lead_first_name,attempts=0)
            payload = {
                "lead_id": lead_id,
                "lead_name": lead_name,
                "lead_first_name": lead_first_name,  
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
            print("insert_or_increment_retry inserted for new successful:",res)
            return res.data[0] if res.data else None
    except Exception as e:
        print("❌ insert_or_increment_retry error:", e, traceback.format_exc())
        return None

# def compute_next_call_time(lead_first_name: str | None):
#     now_ist = datetime.now(IST)
#     policy = get_lead_calling_policy(lead_first_name)

#     if policy["retry_interval_unit"] == "minutes":
#         candidate = now_ist + timedelta(minutes=policy["retry_interval_minutes"])
#     else:
#         candidate = now_ist + timedelta(hours=policy["retry_interval_hours"])

#     return candidate.astimezone(timezone.utc)

def is_within_retry_calling_window(now_ist: datetime) -> bool:
    """
    Retry calls allowed only between 09:00–18:00 IST
    """
    return 9 <= now_ist.hour < 18

def next_retry_window_start(now_ist: datetime) -> datetime:
    """
    Returns next allowed retry start time (09:00 IST)
    """
    # If before 9 AM → today 9 AM
    if now_ist.hour < 9:
        return now_ist.replace(hour=9, minute=0, second=0, microsecond=0)

    # If after 6 PM → tomorrow 9 AM
    if now_ist.hour >= 18:
        next_day = now_ist + timedelta(days=1)
        return next_day.replace(hour=9, minute=0, second=0, microsecond=0)

    # Otherwise, already inside window
    return now_ist


def mark_retry_attempt(lead_id: str, bolna_call_id: str = None, status: str = None):
    """Increment attempts and optionally append bolna_call_id, update last_status and next_call_at."""
    try:
        q = supabase.table("outbound_call_retries").select("*").eq("lead_id", lead_id).execute()
        rows = q.data or []
        now = datetime.now(timezone.utc)
        if not rows:
            # fallback — create
            return insert_or_increment_retry(lead_id, phone="unknown", lead_name=None,lead_first_name=None, reason=status)
        row = rows[0]
        attempts = (row.get("attempts") or 0) + 1
        max_attempts = row.get("max_attempts") or MAX_ATTEMPTS_DEFAULT
        bolna_ids = row.get("bolna_call_ids") or []
        if bolna_call_id:
            bolna_ids = bolna_ids + [bolna_call_id]
        next_call = get_next_allowed_call_time( row.get("lead_first_name"),attempts)
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

def apply_busy_call_override(lead_id: str, busy_call_next: str):
    """
    Stores a ONE-TIME absolute call time that bypasses all retry rules.
    """
    try:
        dt = isoparse(busy_call_next)

        if dt.tzinfo is None:
            dt = IST.localize(dt)

        supabase.table("outbound_call_retries").update({
            "busy_call_at": dt.astimezone(timezone.utc).isoformat(),
            "busy_call_consumed": False,
            "next_call_at": dt.astimezone(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat()
        }).eq("lead_id", lead_id).execute()

        print(f"⏰ Busy override scheduled for {dt}")

        return True
    except Exception as e:
        print("❌ Failed to apply busy_call_next:", e)
        return False


def get_next_allowed_call_time(
    lead_first_name: str | None,
    attempts:int = 1,
    base_time_ist: datetime | None = None
):
    """
    Returns next allowed call time in UTC,
    respecting Sunday 10–12 hard blackout.
    """
    if not base_time_ist:
        base_time_ist = datetime.now(IST)

    # 🔴 HARD BLOCK: Sunday 10–12 (ALL calls)
    if is_sunday_blackout_window(base_time_ist):
        next_try = base_time_ist.replace(
            hour=12, minute=1, second=0, microsecond=0
        )
        return next_try.astimezone(timezone.utc)
    
    # ✅ FIRST CALL → IMMEDIATE
    if attempts == 0:
        return base_time_ist.astimezone(timezone.utc)

    # policy = get_lead_calling_policy(lead_first_name)

    # if policy["retry_interval_unit"] == "minutes":
    #     next_try = base_time_ist + timedelta(
    #         minutes=policy["retry_interval_minutes"]
    #     )
    # else:
    #     next_try = base_time_ist + timedelta(
    #         hours=policy["retry_interval_hours"]
    #     )

    # 🔁 RETRY LOGIC BELOW

    # Step 1: enforce retry calling window (9 AM – 6 PM)
    adjusted_base = next_retry_window_start(base_time_ist)

    # Step 2: add retry interval
    policy = get_lead_calling_policy(lead_first_name)

    if policy["retry_interval_unit"] == "minutes":
        next_try = adjusted_base + timedelta(
            minutes=policy["retry_interval_minutes"]
        )
    else:
        next_try = adjusted_base + timedelta(
            hours=policy["retry_interval_hours"]
        )

    # Step 3: if interval pushed it outside window → move to next 9 AM
    if not is_within_retry_calling_window(next_try):
        next_try = next_retry_window_start(next_try)


    return next_try.astimezone(timezone.utc)


def get_lead_calling_policy(lead_first_name: str | None):
    """
    Returns retry interval and cutoff hour based on lead.
    """
    if lead_first_name and lead_first_name.lower() == "udipth":
        return {
            "retry_interval_minutes": RETRY_INTERVAL_HOURS ,  # minutes
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
       
        agent_id = select_bolna_agent(lead_name, lead_first_name)
            
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

def select_bolna_agent(lead_name: str | None, lead_first_name: str | None) -> str:
    name = (lead_name or "").lower()
    fname = (lead_first_name or "").lower()

    # High-priority / internal / associate agents
    if (
        "udipth" in name
        or "udipth" in fname
    ):
        return "eaa1df90-6dd4-45f3-a6e1-76c3fc01ac4b"
    else:
        if "assoma_" in name:
            return "eaa1df90-6dd4-45f3-a6e1-76c3fc01ac4b"

    # Default sales agent
    return "f11a2955-9639-42bb-b77f-d198f5dc352b"

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


# def can_place_call_now(lead_created_at_str: str | None, attempts: int,lead_first_name: str):
#     """
#     Rule:
#       - No calls allowed after 18:00 IST, except allow 1st call if lead was created after 18:00 (special case).
#       - attempts is current attempts count (0 for first).
#     """
#     now_ist = datetime.now(IST)
#     policy = get_lead_calling_policy(lead_first_name)

#     cutoff_hour = policy["call_cutoff_hour"]

#     # 🔴 SUNDAY BLACKOUT WINDOW
#     if is_sunday_blackout_window(now_ist):
#         return False



#     hour = now_ist.hour

#     if hour < cutoff_hour:
#         return True

#     # if past cutoff and it's the first attempt, allow the first attempt only if lead created after cutoff
#     if attempts == 0 and lead_created_at_str:
#         try:
#             # Bitrix date strings vary; attempt to parse ISO-like
#             from dateutil import parser
#             lead_created = parser.parse(lead_created_at_str).astimezone(IST)
#             if lead_created.hour >= cutoff_hour:
#                 return True
#         except Exception:
#             # If we cannot parse, be conservative -> disallow
#             return False
#     return False

# ----------------- Process due retries -----------------

def process_due_retries(verify_bitrix_lead=True, limit=200):
    """
    Process entries whose next_call_at <= now.
    Returns a list of dicts describing actions.
    """
    logger.info("⏳ Checking retry queue...")
    results = []
    due = get_due_retries(limit=limit)
    logger.info(f"📦 Due retries found: {len(due)}")
    for idx, r in enumerate(due, start=1):
        lead_id = r.get("lead_id")
        logger.info(f"➡️ [{idx}/{len(due)}] Processing lead {lead_id}")
        phone = r.get("phone")
        attempts = r.get("attempts") or 0
        max_attempts = r.get("max_attempts") or MAX_ATTEMPTS_DEFAULT

        if lead_id:
            get_res = requests.get(
            f"{BITRIX_WEBHOOK}crm.lead.get.json",
            params={"id": lead_id},
            timeout=10
        )
        lead_data = get_res.json().get("result", {})
        existing_comments = lead_data.get("COMMENTS") or ""



        # Fetch lead created_at for cutoff logic
        lead_created_at = None
        try:
            # br = requests.get(f"{BITRIX_WEBHOOK}crm.lead.get.json", params={"id": lead_id}, timeout=10)
            lead_data = get_res.json().get("result", {})
            lead_created_at = lead_data.get("DATE_CREATE") or lead_data.get("created_at")
        except Exception as e:
            print("⚠️ could not fetch lead created_at:", e)

        if attempts >= max_attempts:
            # mark paused
            cancel_retry_for_lead(lead_id, reason="max_attempts_reached")
            results.append({"lead_id": lead_id, "action": "paused_max_attempts"})
            continue

        lead_first_name =  r.get("lead_first_name")

        now_utc = datetime.now(timezone.utc)

        # 🔥🔥🔥 BUSY OVERRIDE — ABSOLUTE PRIORITY 🔥🔥🔥
        busy_call_at = r.get("busy_call_at")
        busy_consumed = r.get("busy_call_consumed", False)

        if busy_call_at and not busy_consumed:
            try:
                busy_dt = isoparse(busy_call_at)
            except Exception:
                # ❌ Bad AI extraction → consume override and fall back to normal retries
                supabase.table("outbound_call_retries").update({
                    "busy_call_consumed": True,
                    "busy_call_at": None,
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }).eq("lead_id", lead_id).execute()

                continue

            # Not time yet → wait
            if busy_dt > now_utc:
                continue

            # ⏰ Time reached → place call IMMEDIATELY (bypass all rules)
            logger.info(f"📞 Calling lead {lead_id}")
            bolna_response = place_bolna_call(
                phone=phone,
                lead_id=lead_id,
                lead_name=r.get("lead_name"),
                lead_first_name=lead_first_name
            )
            logger.info(f"📞 Bolna response received for {lead_id}")

            bolna_id = bolna_response.get("id") or bolna_response.get("call_id")

            # ✅ Mark override as consumed (IMPORTANT)
            supabase.table("outbound_call_retries").update({
                "busy_call_consumed": True,
                "busy_call_at": None, 
                "updated_at": datetime.now(timezone.utc).isoformat()
            }).eq("lead_id", lead_id).execute()

            # ❌ Do NOT increment attempts here
            results.append({
                "lead_id": lead_id,
                "action": "busy_override_call_placed",
                "bolna_id": bolna_id
            })

            requests.post(
                f"{BITRIX_WEBHOOK}crm.timeline.comment.add",
                json={
                    "fields": {
                        "ENTITY_ID": lead_id,
                        "ENTITY_TYPE": "lead",
                        "COMMENT": "⏰ Busy override call placed at user-requested time"
                    }
                }
            )


            continue

        if isoparse(r["next_call_at"]) > now_utc:
            # Not time yet → do nothing
            continue

        now_ist = datetime.now(IST)
        if is_sunday_blackout_window(now_ist):
            next_try = get_next_allowed_call_time(lead_first_name)
            supabase.table("outbound_call_retries").update({
                "next_call_at": next_try.isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat()
            }).eq("lead_id", lead_id).execute()
            continue

        # Place call
        bolna_response = place_bolna_call(phone=phone, lead_id=lead_id, lead_name=r.get("lead_name"),lead_first_name=lead_first_name)
        bolna_id = bolna_response.get("id") or bolna_response.get("call_id") or None

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

        # Mark attempt and update bolna id
        mark_retry_attempt(lead_id=lead_id, bolna_call_id=bolna_id, status="scheduled")

        results.append({"lead_id": lead_id, "phone": phone, "bolna_id": bolna_id, "action": "call_scheduled"})
    return results

# ----------------- Functions for call now stage  -----------------

def bitrix_bool(val):
    return str(val).upper() in ("Y", "YES", "1", "TRUE")


def fetch_call_now_leads(limit=50):
    resp = requests.get(
        f"{BITRIX_WEBHOOK}crm.lead.list.json",
        params={
            "filter[STATUS_ID]": "UC_N39RCN",
            "filter[UF_CRM_1766405062574]": "1",
            "select[]": ["ID", "TITLE", "NAME", "PHONE"],
            "start": 0
        },timeout=10
    )
    return resp.json().get("result", [])[:limit]

def process_call_now_leads(limit=50):
    leads = fetch_call_now_leads(limit)
    processed = []

    for lead in leads:
        lead_id = lead["ID"]
        phone = (lead.get("PHONE") or [{}])[0].get("VALUE")
        lead_name = lead.get("TITLE")
        lead_first_name = lead.get("NAME")

        if not phone:
            continue
        

        # 1️⃣ Lock lead
        lock = requests.post(
            f"{BITRIX_WEBHOOK}crm.lead.update.json",
            json={
                "id": lead_id,
                "fields": {
                    "UF_CRM_1766405062574": "0"
                }
            }
        )
        if not lock.ok:
            continue

        # 2️⃣ Enqueue into retry engine
        insert_or_increment_retry(
            lead_id=lead_id,
            phone=phone,
            lead_name=lead_name,
            lead_first_name=lead_first_name,
            reason="call_now_stage"
        )

        # 3️⃣ Move to Unanswered
        requests.post(
            f"{BITRIX_WEBHOOK}crm.lead.update.json",
            json={
                "id": lead_id,
                "fields": {
                    "STATUS_ID": "14"
                }
            }
        )

        processed.append(lead_id)

    return processed
