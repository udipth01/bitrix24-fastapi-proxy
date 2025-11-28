from fastapi import FastAPI, Request
import requests
import os
from supabase import create_client
from datetime import datetime, timedelta
from urllib.parse import parse_qs
import pytz  # install with: pip install pytz
import json
import re

app = FastAPI()

# ---------- Helpers ----------

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


def parse_custom_extractions(raw):
    """
    raw can be:
    - None
    - JSON string: '{"RM_meeting_time": "...", "Webinar_attended": "..."}'
    - dict
    Returns a dict or {}.
    """
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            # If parsing fails, log-like behavior but just return {}
            print("⚠️ Failed to parse custom_extractions JSON:", raw)
            return {}
    return {}


def parse_budget_to_number(budget_str: str | None) -> int | None:
    """
    Parse all Indian-style budget inputs:
    - "60,00,000"
    - "₹60,00,000"
    - "60 lakh", "60 lakhs", "60 lac"
    - "over 10 Lakh"
    - "10-20 Lakh" (→ take max = 20 lakh)
    - "1.5 crore"
    - "sixty lakh" (convert words to numbers)
    """

    if not budget_str:
        return None

    s = budget_str.lower().strip()

    # Remove ₹, rs, whitespace
    s = re.sub(r"[₹, ]", "", s)
    s = s.replace("rs", "").replace("rs.", "").strip()

    # If pure digit now → direct
    if s.isdigit():
        return int(s)

    # Restore original with commas for unit detection
    orig = budget_str.lower()

    # Extract digits (handles "10-20 lakh")
    nums = re.findall(r"\d+(?:\.\d+)?", orig)
    if nums:
        # choose maximum number if ranges
        val = float(max(nums))
    else:
        # WORD BASED numbers (simple mapping)
        word_map = {
            "ten": 10, "twenty": 20, "thirty": 30, "forty": 40,
            "fifty": 50, "sixty": 60, "seventy": 70,
            "eighty": 80, "ninety": 90, "hundred": 100
        }
        words = orig.split()
        vals = [word_map[w] for w in words if w in word_map]
        if vals:
            val = max(vals)
        else:
            return None

    # Detect lakh / crore
    if "crore" in orig or "cr" in orig:
        multiplier = 10_000_000
    elif "lakh" in orig or "lac" in orig:
        multiplier = 100_000
    else:
        # If we see a number like 6000000 (7 digits → lakhs) but no word,
        # assume it is final amount
        return int(val)

    return int(val * multiplier)


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


def find_deal_for_lead(lead_id):
    url = f"{BITRIX_WEBHOOK}crm.deal.list.json"

    payload = {
        "filter": {"LEAD_ID": lead_id},
        "select": ["ID", "TITLE", "STAGE_ID", "CATEGORY_ID", "OPPORTUNITY"],
        "order": {"ID": "DESC"}   # get newest deal
    }

    res = requests.post(url, json=payload)
    deals = res.json().get("result", [])

    if deals:
        return deals[0]["ID"]   # newest deal

    return None


# ---------- Config ----------

BOLNA_TOKEN = os.getenv("BOLNA_API_KEY")
BITRIX_WEBHOOK = os.getenv("BITRIX_WEBHOOK")
SUPABASE_URL = "https://fbputkobdsqorfdizbyf.supabase.co"
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


# ---------- Bitrix Lead → Bolna trigger (unchanged) ----------

@app.post("/bolna-proxy")
async def bolna_proxy(request: Request):
    raw_body = await request.body()
    raw_text = raw_body.decode("utf-8")
    print("🔹 Raw incoming body:", raw_text)

    payload = parse_qs(raw_text)
    print("🔹 Parsed payload:", payload)

    lead_id = None
    if "data[FIELDS][ID]" in payload:
        lead_id = payload["data[FIELDS][ID]"][0]
    elif "id" in payload:
        lead_id = payload["id"][0]

    if not lead_id:
        return {"status": "error", "reason": "Lead ID missing"}

    lead_url = f"{BITRIX_WEBHOOK}crm.lead.get.json"
    response = requests.get(lead_url, params={"id": lead_id})
    if response.status_code != 200:
        return {
            "status": "error",
            "reason": "Bitrix fetch failed",
            "bitrix_response": response.text,
        }

    lead_data = response.json().get("result", {})
    phone = None
    if lead_data.get("PHONE"):
        phone = lead_data["PHONE"][0].get("VALUE")

    lead_name = lead_data.get("TITLE")
    print(f"✅ Lead name: {lead_name}, phone: {phone}")

    try:
        supabase.table("webhook_logs").insert({
            "timestamp": datetime.utcnow().isoformat(),
            "lead_id": lead_data.get("ID"),
            "phone": phone,
            "name": lead_name,
            "payload": lead_data
        }).execute()
    except Exception as e:
        print("❌ Supabase insert error:", str(e))

    if "swciad_" not in lead_name.lower() and "udipth" not in lead_name.lower():
        return {
            "status": "skipped",
            "reason": "Lead name does not contain 'SWCIAD_' or 'udipth'",
        }

    # Select agent based on lead_name
    if "udipth" in lead_name.lower():
        agent_id = "f11a2955-9639-42bb-b77f-d198f5dc352b"
    else:
        agent_id = "950c36e8-92ed-4a21-9764-03267e2f6039"

    if phone:
        bolna_payload = {
            "agent_id": agent_id,
            "recipient_phone_number": phone,
            "from_phone_number": "+918035316588",
            "user_data": {
                "lead_id": lead_data.get("ID"),
                "lead_name": lead_name
            }
        }
        headers = {
            "Authorization": f"Bearer {BOLNA_TOKEN}",
            "Content-Type": "application/json"
        }
        bolna_response = requests.post(
            "https://api.bolna.ai/call",
            json=bolna_payload,
            headers=headers
        )
        print("📞 Bolna.ai response:", bolna_response.text)
        return {"status": "forwarded", "bolna_response": bolna_response.text}

    return {"status": "skipped", "reason": "No phone number found"}


# ---------- Post-call webhook (Bolna → Supabase + Bitrix lead + deal+activity) ----------

@app.post("/post-call-webhook")
async def post_call_webhook(request: Request):
    """Receives post-call status from Bolna.ai and updates Supabase + Bitrix"""
    data = await request.json()
    print("📥 Post-call webhook received:", data)

    # Extract lead info safely
    context = data.get("context_details") or {}
    recipient_data = context.get("recipient_data") or {}
    lead_id = recipient_data.get("lead_id")
    lead_name = recipient_data.get("lead_name")
    recipient_phone = context.get("recipient_phone_number")

    # Extracted tags
    extracted_data = data.get("extracted_data", {}) or {}
    user_name = extracted_data.get("user_name", "Unknown")
    interested = extracted_data.get("interested", "NA")

    # ---- Custom Extractions ----
    custom_extractions_raw = data.get("custom_extractions")
    ce = parse_custom_extractions(custom_extractions_raw)
    rm_meeting_time_raw = ce.get("RM_meeting_time")
    webinar_attended = ce.get("Webinar_attended") or ce.get("webinar_attended")
    investment_budget_raw = (
        ce.get("Investment_amount")      # numeric value like "5500000"
        or ce.get("Investment_Budget")
        or ce.get("investment_budget")
        or ce.get("Investment_Category")  # fallback description "over 10 Lakh"
    )
    webinar_attended_norm = (webinar_attended or "").strip().lower()

    # Parse budget to numeric INR
    investment_budget_value = parse_budget_to_number(investment_budget_raw)

    print(
        f"🔎 Parsed custom_extractions → "
        f"RM_meeting_time={rm_meeting_time_raw}, "
        f"Webinar_attended={webinar_attended}, "
        f"Investment_Budget={investment_budget_raw} "
        f"(parsed={investment_budget_value})"
    )

    # Call metadata
    call_summary = data.get("summary", "")
    transcript = data.get("transcript", "")
    status = data.get("status")
    conversation_duration = data.get("conversation_duration")
    total_cost = data.get("total_cost")

    # Telephony details
    telephony_data = data.get("telephony_data", {}) or {}
    to_number = telephony_data.get("to_number")
    from_number = telephony_data.get("from_number")
    recording_url = telephony_data.get("recording_url")
    provider_call_id = telephony_data.get("provider_call_id")
    call_type = telephony_data.get("call_type")
    telephony_provider = telephony_data.get("provider")

    if status == "completed":
        # ✅ Save in Supabase (bolna_call_logs table)
        try:
            payload = {
                "bolna_id": data.get("id"),
                "agent_id": data.get("agent_id"),
                "batch_id": data.get("batch_id"),
                "campaign_id": data.get("campaign_id"),

                "created_at": data.get("created_at"),
                "updated_at": data.get("updated_at"),
                "scheduled_at": data.get("scheduled_at"),
                "rescheduled_at": data.get("rescheduled_at"),

                "status": status,
                "answered_by_voice_mail": data.get("answered_by_voice_mail"),
                "conversation_duration": conversation_duration,
                "total_cost": total_cost,
                "transcript": transcript,
                "summary": call_summary,
                "error_message": data.get("error_message"),

                # extracted tags
                "user_name": user_name,
                "interested": interested,

                # custom_extractions
                "rm_meeting_time": rm_meeting_time_raw,
                "webinar_attended": webinar_attended,
                "investment_budget_raw": investment_budget_raw,
                "investment_budget_value": investment_budget_value,

                # telephony data
                "telephony_duration": telephony_data.get("duration"),
                "to_number": to_number,
                "from_number": from_number,
                "recording_url": recording_url,
                "hosted_telephony": telephony_data.get("hosted_telephony"),
                "provider_call_id": provider_call_id,
                "call_type": call_type,
                "telephony_provider": telephony_provider,
                "hangup_by": telephony_data.get("hangup_by"),
                "hangup_reason": telephony_data.get("hangup_reason"),
                "hangup_provider_code": telephony_data.get("hangup_provider_code"),

                # lead mapping
                "lead_id": lead_id,
                "lead_name": lead_name,
                "recipient_phone_number": recipient_phone,

                # breakdowns
                "usage_breakdown": data.get("usage_breakdown"),
                "cost_breakdown": data.get("cost_breakdown"),

                # metadata
                "provider": data.get("provider"),
                "raw_payload": data,
            }

            res = supabase.table("bolna_call_logs").insert(payload).execute()
            print("✅ Supabase insert success:", res)
        except Exception as e:
            print("❌ Supabase insert error:", str(e))

        # ✅ Update Bitrix comments log on LEAD
        if lead_id:
            get_res = requests.get(
                f"{BITRIX_WEBHOOK}crm.lead.get.json",
                params={"id": lead_id},
            )
            lead_data = get_res.json().get("result", {})
            existing_comments = lead_data.get("COMMENTS") or ""

            timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
            new_entry = f"<p><b>Post-call Update ({timestamp}):</b></p>"
            new_entry += f"<p>Transcript: {transcript}</p>"
            new_entry += f"<p>Interest: {interested}</p>"

            if webinar_attended is not None:
                new_entry += f"<p>Webinar attended: {webinar_attended}</p>"
            if investment_budget_raw:
                new_entry += f"<p>Investment Budget: {investment_budget_raw}</p>"

            # 🔗 Wrap recording link in HTML so Bitrix doesn't truncate
            if recording_url:
                new_entry += (
                    f'<p>Recording: <a href="{recording_url}" target="_blank">'
                    f"{recording_url}</a> </p>"
                )

            if call_summary:
                new_entry += f"<p>Summary: {call_summary}</p>"

            updated_comments = existing_comments + new_entry

            # Base fields for lead update
            update_fields = {
                "COMMENTS": updated_comments,
                "UF_CRM_1586952775435": "136"   # ⭐ Required for deal creation
            }

            # ------------------------------------------------------------
            #              FINAL FLOW BASED ON webinar_attended_norm
            # ------------------------------------------------------------

            # ---------- CASE 1: Webinar attended → YES ----------
            if webinar_attended_norm == "yes" and investment_budget_value >=1000000:
                print("🎉 Webinar attended = YES → Create deal + RM meeting + comments")

                # Mark attended
                update_fields["UF_CRM_1764239159240"] = "Y"
                update_fields["STATUS_ID"] = "PROCESSED"

                # Update lead FIRST
                lead_update_payload = {"id": lead_id, "fields": update_fields}
                requests.post(
                    f"{BITRIX_WEBHOOK}crm.lead.update.json",
                    json=lead_update_payload
                )

                # Allow Bitrix automation to create deal (1–2 sec)
                import time
                time.sleep(2)

                # Find deal created by automation
                deal_id = find_deal_for_lead(lead_id)

                # ---------- Add timeline comments inside the deal ----------
                if deal_id:

                    # Transcript
                    requests.post(
                        f"{BITRIX_WEBHOOK}crm.timeline.comment.add",
                        json={
                            "fields": {
                                "ENTITY_ID": deal_id,
                                "ENTITY_TYPE": "deal",
                                "COMMENT": f"<b>Transcript</b><br>{transcript}"
                            }
                        }
                    )

                    # Summary
                    requests.post(
                        f"{BITRIX_WEBHOOK}crm.timeline.comment.add",
                        json={
                            "fields": {
                                "ENTITY_ID": deal_id,
                                "ENTITY_TYPE": "deal",
                                "COMMENT": f"<b>Summary</b><br>{call_summary}"
                            }
                        }
                    )

                    # Call recording link
                    if recording_url:
                        requests.post(
                            f"{BITRIX_WEBHOOK}crm.timeline.comment.add",
                            json={
                                "fields": {
                                    "ENTITY_ID": deal_id,
                                    "ENTITY_TYPE": "deal",
                                    "COMMENT": (
                                        f"<b>Call Recording</b><br>"
                                        f'<a href="{recording_url}" target="_blank">Click to Listen</a>'
                                    )
                                }
                            }
                        )

                    # ---------- Update Deal Opportunity ----------
                    if investment_budget_value:
                        requests.post(
                            f"{BITRIX_WEBHOOK}crm.deal.update.json",
                            json={
                                "id": deal_id,
                                "fields": {
                                    "OPPORTUNITY": investment_budget_value,
                                    "CURRENCY_ID": "INR",
                                    "IS_MANUAL_OPPORTUNITY": "Y"
                                }
                            }
                        )

                    # ---------- Create RM Meeting Activity ----------
                    start_time, date_only = parse_rm_meeting_time(rm_meeting_time_raw)

                    if start_time:
                        dt_start = datetime.strptime(start_time, "%Y-%m-%dT%H:%M:%S")
                        dt_start = dt_start - timedelta(minutes=150)
                        dt_end = dt_start + timedelta(minutes=30)

                        act = {
                            "fields": {
                                "OWNER_TYPE_ID": 2,  # deal
                                "OWNER_ID": deal_id,
                                "TYPE_ID": 2,
                                "SUBJECT": "Scheduled RM Call – Auto-created from Voicebot",
                                "START_TIME": dt_start.strftime("%Y-%m-%dT%H:%M:%S"),
                                "END_TIME": dt_end.strftime("%Y-%m-%dT%H:%M:%S"),
                                "DESCRIPTION": (
                                    f"RM Meeting scheduled from call.\n"
                                    f"RM_meeting_time_raw: {rm_meeting_time_raw}\n"
                                    f"Parsed date: {date_only}\n"
                                    f"Investment Budget: {investment_budget_raw}\n"
                                ),
                                "DIRECTION": 2,
                                "COMMUNICATIONS": [
                                    {
                                        "VALUE": recipient_phone or to_number,
                                        "ENTITY_TYPE_ID": 2,
                                        "ENTITY_ID": deal_id,
                                    }
                                ],
                            }
                        }

                        requests.post(
                            f"{BITRIX_WEBHOOK}crm.activity.add.json",
                            json=act
                        )

                return {"status": "success", "flow": "deal_created"}

            # ------------------------------------------------------------
            #         CASE 2: Webinar attended != YES → update LEAD only
            # ------------------------------------------------------------
            print("⚠️ Webinar attended != YES → Update lead, DO NOT create deal")

            update_fields["UF_CRM_1764323136141"] = "Y"
            if investment_budget_value<1000000:
                update_fields["STATUS_ID"] = "JUNK"   # Move to Junk
            else:
                update_fields["STATUS_ID"] = "14"   # Move to Unanswered to trigger automation

            # ---------- Put Opportunity inside LEAD (NOT DEAL) ----------
            if investment_budget_value:
                update_fields["OPPORTUNITY"] = investment_budget_value
                update_fields["CURRENCY_ID"] = "INR"

            # ---------- Create RM Meeting Activity directly under LEAD ----------
            start_time, date_only = parse_rm_meeting_time(rm_meeting_time_raw)

            if start_time:
                dt_start = datetime.strptime(start_time, "%Y-%m-%dT%H:%M:%S")
                dt_start = dt_start - timedelta(minutes=150)
                dt_end = dt_start + timedelta(minutes=30)

                lead_activity = {
                    "fields": {
                        "OWNER_TYPE_ID": 1,  # Lead
                        "OWNER_ID": lead_id,
                        "TYPE_ID": 2,
                        "SUBJECT": "Scheduled RM Call – Auto-created from Voicebot",
                        "START_TIME": dt_start.strftime("%Y-%m-%dT%H:%M:%S"),
                        "END_TIME": dt_end.strftime("%Y-%m-%dT%H:%M:%S"),
                        "DESCRIPTION": (
                            f"RM Meeting scheduled from call.\n"
                            f"RM_meeting_time_raw: {rm_meeting_time_raw}\n"
                            f"Parsed date: {date_only}\n"
                            f"Investment Budget: {investment_budget_raw}\n"
                        ),
                        "DIRECTION": 2,
                        "COMMUNICATIONS": [
                            {
                                "VALUE": recipient_phone or to_number,
                                "ENTITY_TYPE_ID": 1,
                                "ENTITY_ID": lead_id,
                            }
                        ],
                    }
                }

                requests.post(
                    f"{BITRIX_WEBHOOK}crm.activity.add.json",
                    json=lead_activity
                )

            # ---------- Update LEAD ----------
            lead_update_payload = {"id": lead_id, "fields": update_fields}

            requests.post(
                f"{BITRIX_WEBHOOK}crm.lead.update.json",
                json=lead_update_payload
            )

            return {"status": "success", "flow": "lead_updated_only"}

        return {"status": "success"}

    return {"status": status}


@app.get("/health")
def health_check():
    return {"status": "ok"}
