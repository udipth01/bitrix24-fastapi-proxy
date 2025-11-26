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
    Convert things like:
      - "over 10 Lakh"
      - "10-20 Lakh"
      - "more than 50 Lakh"
    into an integer amount in INR.
    Rule: If a range is given, take the MAX value.
    """
    if not budget_str:
        return None

    s = budget_str.lower()
    # Find all integer numbers
    nums = re.findall(r"\d+", s)
    if not nums:
        return None

    # Take max value if multiple
    n = max(int(x) for x in nums)

    # Detect unit
    if "crore" in s or "cr" in s:
        multiplier = 10_000_000
    elif "lakh" in s or "lac" in s:
        multiplier = 100_000
    else:
        multiplier = 1

    return n * multiplier


def parse_rm_meeting_time(rm_str: str | None):
    """
    Returns:
      - start_dt: Bitrix datetime 'YYYY-MM-DDTHH:MM:SS'
      - date_only: 'YYYY-MM-DD'
    Supports:
      - "tomorrow 14:00"
      - "2025-11-27 15:00"
      - "27-11-2025 15:00"
      - "15:00 27/11/2025"
      - "15:00 27-11-2025"
    """
    if not rm_str:
        return None, None

    s = rm_str.lower().strip()
    ist_zone = pytz.timezone("Asia/Kolkata")
    now_ist = datetime.now(ist_zone)

    # -------- CASE 1: "tomorrow 14:00" --------
    m = re.search(r"(\d{1,2}):(\d{2})", s)
    if "tomorrow" in s and m:
        hour = int(m.group(1))
        minute = int(m.group(2))
        dt = (now_ist + timedelta(days=1)).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        return dt.strftime("%Y-%m-%dT%H:%M:%S"), dt.strftime("%Y-%m-%d")

    # -------- CASE 2: "HH:MM DD/MM/YYYY" or "HH:MM DD-MM-YYYY" --------
    m = re.match(r"(\d{1,2}):(\d{2})\s+(\d{1,2})[/-](\d{1,2})[/-](\d{4})", s)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        dd, mm, yyyy = int(m.group(3)), int(m.group(4)), int(m.group(5))
        dt = ist_zone.localize(datetime(yyyy, mm, dd, hour, minute, 0))
        return dt.strftime("%Y-%m-%dT%H:%M:%S"), dt.strftime("%Y-%m-%d")

    # -------- CASE 3: "YYYY-MM-DD HH:MM" or "DD-MM-YYYY HH:MM" --------
    for fmt in ["%Y-%m-%d %H:%M", "%d-%m-%Y %H:%M"]:
        try:
            dt = datetime.strptime(rm_str, fmt)
            dt = ist_zone.localize(dt)
            return dt.strftime("%Y-%m-%dT%H:%M:%S"), dt.strftime("%Y-%m-%d")
        except:
            pass

    print("⚠️ Could not parse RM_meeting_time:", rm_str)
    return None, None


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
        return {"status": "error", "reason": "Bitrix fetch failed", "bitrix_response": response.text}

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
        return {"status": "skipped", "reason": "Lead name does not contain 'SWCIAD_' or 'udipth'"}

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
        bolna_response = requests.post("https://api.bolna.ai/call", json=bolna_payload, headers=headers)
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
    investment_budget_raw = ce.get("Investment_Budget") or ce.get("investment_budget")
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

        # ✅ Update Bitrix comments log
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
            update_fields = {"COMMENTS": updated_comments}

            # If webinar attended YES → mark lead PROCESSED
            if webinar_attended_norm == "yes":
                update_fields["STATUS_ID"] = "PROCESSED"

            # Update lead in Bitrix
            lead_update_payload = {"id": lead_id, "fields": update_fields}
            lead_update_res = requests.post(
                f"{BITRIX_WEBHOOK}crm.lead.update.json", json=lead_update_payload
            )
            print("📤 Bitrix lead update response:", lead_update_res.text)
            print("📤 Bitrix lead payload:", lead_update_payload)

            # ---------- Deal + Activity creation if Webinar_attended == Yes ----------
            if webinar_attended_norm == "yes":
                # 1️⃣ Create Deal
                deal_fields = {
                    "TITLE": f"ILTS – {lead_name or 'Voicebot Lead'}",
                    "LEAD_ID": lead_id,
                    "CATEGORY_ID": 0,  # default pipeline; adjust if you have custom pipeline
                    "STAGE_ID": "NEW",  # default new stage; modify if required
                    "CURRENCY_ID": "INR",
                }

                if investment_budget_value is not None:
                    deal_fields["OPPORTUNITY"] = investment_budget_value

                deal_add_payload = {"fields": deal_fields}
                deal_res = requests.post(
                    f"{BITRIX_WEBHOOK}crm.deal.add.json", json=deal_add_payload
                )
                print("📤 Bitrix deal add response:", deal_res.text)

                deal_result = deal_res.json()
                deal_id = deal_result.get("result")

                # 2️⃣ Create Activity for RM meeting if we have a time and deal_id
                start_time, date_only = parse_rm_meeting_time(rm_meeting_time_raw)

                if deal_id and rm_meeting_time_raw:
                    dt_start = datetime.strptime(start_time, "%Y-%m-%dT%H:%M:%S")
                    dt_end = dt_start + timedelta(minutes=30)

                    activity_fields = {
                        "OWNER_TYPE_ID": 2,
                        "OWNER_ID": deal_id,
                        "TYPE_ID": 2,
                        "SUBJECT": "Scheduled RM Call – Auto-created from Voicebot",
                        "START_TIME": dt_start.strftime("%Y-%m-%dT%H:%M:%S"),
                        "END_TIME": dt_end.strftime("%Y-%m-%dT%H:%M:%S"),
                        "DESCRIPTION": (
                            f"RM Meeting scheduled by voicebot.\n"
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

                    activity_payload = {"fields": activity_fields}
                    act_res = requests.post(
                        f"{BITRIX_WEBHOOK}crm.activity.add.json",
                        json=activity_payload
                    )
                    print("📤 Activity response:", act_res.text)
                    print("📤 Bitrix activity payload:", activity_payload)

                # --- Add timeline comments to the deal ---
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

                    # Recording Attachment
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


        return {"status": "success"}

    return {"status": status}


@app.get("/health")
def health_check():
    return {"status": "ok"}
