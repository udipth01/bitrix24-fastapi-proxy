from fastapi import FastAPI, Request
import requests
import os
from supabase import create_client
from datetime import datetime
from urllib.parse import parse_qs

app = FastAPI()

# 🔐 Environment variables
BOLNA_TOKEN = os.getenv("BOLNA_API_KEY")
SUPABASE_URL = "https://fbputkobdsqorfdizbyf.supabase.co"
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# 🔗 Supabase client
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

@app.post("/bolna-proxy")
async def bolna_proxy(request: Request):
    # ✅ Parse Bitrix24 form-encoded payload
    body = await request.body()
    parsed = parse_qs(body.decode("utf-8"))

    lead_id = parsed.get("data[FIELDS][ID]", [None])[0]
    domain = parsed.get("auth[domain]", [None])[0]
    token = parsed.get("auth[application_token]", [None])[0]

    if not all([lead_id, domain, token]):
        return {"status": "error", "reason": "Missing lead_id, domain, or token"}

    # 🔍 Fetch full lead details from Bitrix24 REST API
    lead_url = f"https://{domain}/rest/crm.lead.get.json"
    response = requests.get(lead_url, params={
        "id": lead_id,
        "auth": token
    })

    if response.status_code != 200:
        return {"status": "error", "reason": "Failed to fetch lead from Bitrix24", "bitrix_response": response.text}

    lead_data = response.json().get("result", {})

    # 📝 Log to Supabase
    try:
        supabase.table("webhook_logs").insert({
            "timestamp": datetime.utcnow().isoformat(),
            "lead_id": lead_data.get("ID"),
            "phone": lead_data.get("PHONE"),
            "name": lead_data.get("TITLE"),
            "payload": lead_data
        }).execute()
    except Exception as e:
        print("Supabase insert error:", str(e))

    # ✅ Conditional call to Bolna.ai
    if "udipth" not in lead_data.get("TITLE", "").lower():
        return {"status": "skipped", "reason": "Lead name does not contain 'udipth'"}

    payload = {
        "agent_id": "950c36e8-92ed-4a21-9764-03267e2f6039",
        "recipient_phone_number": lead_data.get("PHONE"),
        "from_phone_number": "+918035316588",
        "user_data": {
            "variable1": lead_data.get("ID"),
            "variable2": lead_data.get("TITLE"),
            "variable3": f"Lead from Bitrix24: {lead_data.get('TITLE')}"
        }
    }

    headers = {
        "Authorization": f"Bearer {BOLNA_TOKEN}",
        "Content-Type": "application/json"
    }

    bolna_response = requests.post("https://api.bolna.ai/call", json=payload, headers=headers)

    return {
        "status": "forwarded",
        "bolna_response": bolna_response.text
    }

@app.get("/health")
def health_check():
    return {"status": "ok"}