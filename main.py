from fastapi import FastAPI, Request
import requests
import os
from supabase import create_client
from datetime import datetime
from urllib.parse import parse_qs

app = FastAPI()

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
    
    if "udipth" not in lead_name:
        return {"status": "skipped", "reason": "Lead name does not contain 'udipth'"}

    if phone:
        bolna_payload = {
            "agent_id": "950c36e8-92ed-4a21-9764-03267e2f6039",  # Replace with your agent ID
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

    lead_id = data.get("user_data", {}).get("lead_id")
    user_name = data.get("extractions", {}).get("user_name", "Unknown")
    interested = data.get("extractions", {}).get("interested", "NA")
    call_summary = data.get("summary", "")

    # ✅ Save in Supabase (call_logs table)
    try:
        supabase.table("call_logs").insert({
            "timestamp": datetime.utcnow().isoformat(),
            "lead_id": lead_id,
            "user_name": user_name,
            "interested": interested,
            "summary": call_summary,
            "raw_payload": data
        }).execute()
    except Exception as e:
        print("❌ Supabase insert error (call_logs):", str(e))

    # ✅ Update Bitrix comments
    if lead_id:
        get_res = requests.get(f"{BITRIX_WEBHOOK}crm.lead.get.json", params={"id": lead_id})
        lead_data = get_res.json().get("result", {})
        existing_comments = lead_data.get("COMMENTS", "")

        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        new_entry = f"<p><b>Post-call Update ({timestamp}):</b></p>"
        new_entry += f"<p>User: {user_name}</p>"
        new_entry += f"<p>Interest: {interested}</p>"
        if call_summary:
            new_entry += f"<p>Summary: {call_summary}</p>"

        updated_comments = existing_comments + new_entry

        update_payload = {
            "id": lead_id,
            "fields": {"COMMENTS": updated_comments}
        }
        res = requests.post(f"{BITRIX_WEBHOOK}crm.lead.update.json", data=update_payload)
        print("📤 Bitrix update response:", res.text)

    return {"status": "success"}


@app.post("/update-lead-status")
async def update_lead_status(request: Request):
    data = await request.json()
    print("📥 Bolna function call received:", data)

    lead_id = data.get("lead_id")
    status = data.get("qualification_status")
    notes = data.get("notes", "")

    print(f"📌 Updating lead {lead_id} -> Status: {status} | Notes: {notes}")

    try:
        supabase.table("lead_status_logs").insert({
            "timestamp": datetime.utcnow().isoformat(),
            "lead_id": lead_id,
            "status": status,
            "notes": notes,
            "raw_payload": data
        }).execute()
    except Exception as e:
        print("❌ Supabase log insert error:", str(e))

    if not lead_id or not status:
        return {"status": "error", "reason": "Missing lead_id or qualification_status"}

    get_res = requests.get(f"{BITRIX_WEBHOOK}crm.lead.get.json", params={"id": lead_id})
    lead_data = get_res.json().get("result", {})
    existing_comments = lead_data.get("COMMENTS", "")

    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    new_entry = f"<p><b>AI Qualification ({timestamp}):</b> {status}</p>"
    if notes:
        new_entry += f"<p><b>Notes:</b> {notes}</p>"
    updated_comments = existing_comments + new_entry

    update_payload = {
        "id": lead_id,
        "fields": {"COMMENTS": updated_comments}
    }
    res = requests.post(f"{BITRIX_WEBHOOK}crm.lead.update.json", data=update_payload)
    print("📤 Bitrix update response:", res.text)

    try:
        supabase.table("webhook_logs").update({
            "comments": updated_comments
        }).eq("lead_id", lead_id).execute()
    except Exception as e:
        print("❌ Supabase comments update error:", str(e))

    return {"status": "success", "bitrix_response": res.json()}


@app.get("/health")
def health_check():
    return {"status": "ok"}
