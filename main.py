from fastapi import FastAPI, Request
import requests
import os
from supabase import create_client
from datetime import datetime

app = FastAPI()

# 🔐 Environment variables (set these in Render dashboard)
BOLNA_TOKEN = os.getenv("BOLNA_API_KEY")
BITRIX_WEBHOOK = "https://finideas.bitrix24.in/rest/24/hdyr7b0qithutnpx/"
SUPABASE_URL = "https://fbputkobdsqorfdizbyf.supabase.co"
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# 🔗 Supabase client
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

@app.post("/bolna-proxy")
async def bolna_proxy(request: Request):
    # Try reading JSON payload first
    try:
        payload = await request.json()
    except:
        # Fallback for form-data
        form_data = await request.form()
        payload = dict(form_data)

    print("🔹 Incoming Bitrix payload:", payload)

    # Extract lead ID
    lead_id = payload.get("data[FIELDS][ID]") or payload.get("id")
    if not lead_id:
        return {"status": "error", "reason": "Lead ID missing"}

    # Fetch lead details from Bitrix
    lead_url = f"{BITRIX_WEBHOOK}crm.lead.get.json"
    response = requests.get(lead_url, params={"id": lead_id})
    if response.status_code != 200:
        return {"status": "error", "reason": "Bitrix fetch failed", "bitrix_response": response.text}

    lead_data = response.json().get("result", {})
    phone = None
    if lead_data.get("PHONE"):
        phone = lead_data["PHONE"][0].get("VALUE")

    print(f"✅ Lead name: {lead_data.get('TITLE')}, phone: {phone}")

    # 📝 Log to Supabase
    try:
        supabase.table("webhook_logs").insert({
            "timestamp": datetime.utcnow().isoformat(),
            "lead_id": lead_data.get("ID"),
            "phone": phone,
            "name": lead_data.get("TITLE"),
            "payload": lead_data
        }).execute()
    except Exception as e:
        print("❌ Supabase insert error:", str(e))

    # ✅ Send to Bolna.ai
    if phone:
        bolna_payload = {
            "agent_id": "950c36e8-92ed-4a21-9764-03267e2f6039",  # replace with your agent ID
            "recipient_phone_number": phone,
            "from_phone_number": "+918035316588",  # replace with your from number
            "user_data": {
                "lead_id": lead_data.get("ID"),
                "lead_name": lead_data.get("TITLE")
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

@app.get("/health")
def health_check():
    return {"status": "ok"}
