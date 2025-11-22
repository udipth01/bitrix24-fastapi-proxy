from fastapi import FastAPI, Request
import requests
import os
from supabase import create_client
from datetime import datetime
from urllib.parse import parse_qs


app = FastAPI()

from datetime import datetime
import pytz  # install with: pip install pytz

def utc_to_ist(utc_timestamp: str, fmt="%Y-%m-%dT%H:%M:%S%z"):
    """
    Convert a UTC timestamp string to IST timezone.
    
    :param utc_timestamp: The UTC timestamp string (e.g., '2025-08-19T09:30:00+0000')
    :param fmt: The input format of timestamp
    :return: Converted IST datetime string
    """
    # Define timezones
    utc_zone = pytz.timezone("UTC")
    ist_zone = pytz.timezone("Asia/Kolkata")
    
    # Parse input timestamp in UTC
    utc_dt = datetime.strptime(utc_timestamp, fmt)
    utc_dt = utc_zone.localize(utc_dt)
    
    # Convert to IST
    ist_dt = utc_dt.astimezone(ist_zone)
    
    return ist_dt.strftime("%Y-%m-%d %H:%M:%S")



# 🔐 Environment variables (set these in Render)
BOLNA_TOKEN = os.getenv("BOLNA_API_KEY")
BITRIX_WEBHOOK = "https://finideas.bitrix24.in/rest/24/hdyr7b0qithutnpx/"
SUPABASE_URL = "https://fbputkobdsqorfdizbyf.supabase.co"
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# 🔗 Supabase client
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

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
        agent_id = "35a2f74e-0401-4b5b-8d28-59a73fc05dd3"
    else:
        agent_id = "950c36e8-92ed-4a21-9764-03267e2f6039"
        


    if phone:
        bolna_payload = {
            "agent_id": agent_id,  # Replace with your agent ID
            "recipient_phone_number": phone,
            "from_phone_number": "+918035316588",  # Replace with your caller number
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


@app.post("/post-call-webhook")
async def post_call_webhook(request: Request):
    """Receives post-call status from Bolna.ai and updates Supabase + Bitrix"""
    data = await request.json()
    print("📥 Post-call webhook received:", data)

    # Extract lead info safely
    context = data.get("context_details") or {}

    recipient_data = context.get("recipient_data") or {}  # if None → {}
    lead_id = recipient_data.get("lead_id")
    lead_name = recipient_data.get("lead_name")
    recipient_phone = context.get("recipient_phone_number")

    # Extracted tags
    extracted_data = data.get("extracted_data", {}) or {}
    user_name = extracted_data.get("user_name", "Unknown")
    interested = extracted_data.get("interested", "NA")

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

    if status == 'completed':
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
                "raw_payload": data
            }

            res = supabase.table("bolna_call_logs").insert(payload).execute()
            print("✅ Supabase insert success:", res)
        except Exception as e:
            print("❌ Supabase insert error:", str(e))

        # ✅ Update Bitrix comments log
        if lead_id:
            get_res = requests.get(
                f"{BITRIX_WEBHOOK}crm.lead.get.json",
                params={"id": lead_id}
            )
            lead_data = get_res.json().get("result", {})
            existing_comments = lead_data.get("COMMENTS") or ""


            timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
            new_entry = f"<p><b>Post-call Update ({timestamp}):</b></p>"
            new_entry += f"<p>Transcript: {transcript}</p>"
            new_entry += f"<p>Interest: {interested}</p>"

            # 🔗 Wrap recording link in HTML so Bitrix doesn't truncate
            if recording_url:
                new_entry += f'<p>Recording: <a href="{recording_url}" target="_blank">{recording_url}</a> </p>'

            if call_summary:
                new_entry += f"<p>Summary: {call_summary}</p>"

            updated_comments = existing_comments + new_entry

            # ✅ Base payload for updating lead
            update_fields = {
                "COMMENTS": updated_comments
            }


            # ✅ If interested, move lead status to "CONVERTED" (Deal)
            if interested.lower() == "interested":
               # <-- update_fields["STATUS_ID"] = "CONVERTED"   
                update_fields["STATUS_ID"] = "PROCESSED"   # <-- check your Bitrix status code for "Deal"
            update_payload = {
                "id": lead_id,
                "fields": update_fields
            }

            res = requests.post(
                f"{BITRIX_WEBHOOK}crm.lead.update.json",
                json=update_payload
            )
            print("📤 Bitrix update response:", res.text)
            print("📤 Bitrix payload:", update_payload)

            # res = requests.post(
            #     f"{BITRIX_WEBHOOK}crm.lead.update.json",
            #     json=update_payload  # <-- use json= instead of data=
            # )
            # print("📤 Bitrix update response:", res.text)

        return {"status": "success"}
    return {"status": status}



@app.get("/health")
def health_check():
    return {"status": "ok"}
