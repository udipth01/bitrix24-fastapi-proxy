from fastapi import FastAPI, Request
import requests
import os
from supabase import create_client
from datetime import datetime

app = FastAPI()

# 🔐 Environment variables
BOLNA_TOKEN = os.getenv("BOLNA_API_KEY")
SUPABASE_URL = "https://fbputkobdsqorfdizbyf.supabase.co"
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# 🔗 Supabase client
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

@app.post("/bolna-proxy")
async def bolna_proxy(request: Request):
    bitrix_data = await request.json()
    lead_name = bitrix_data.get("TITLE", "").lower()

    # 📝 Log to Supabase regardless of condition
    supabase.table("webhook_logs").insert({
        "timestamp": datetime.utcnow().isoformat(),
        "lead_id": bitrix_data.get("ID"),
        "phone": bitrix_data.get("PHONE"),
        "name": bitrix_data.get("TITLE"),
        "payload": bitrix_data
    }).execute()

    # ✅ Conditional call to Bolna.ai
    if "udipth" not in lead_name:
        return {"status": "skipped", "reason": "Lead name does not contain 'udipth'"}

    payload = {
        "agent_id": "950c36e8-92ed-4a21-9764-03267e2f6039",
        "recipient_phone_number": bitrix_data.get("PHONE"),
        "from_phone_number": "+918035316588",
        "user_data": {
            "variable1": bitrix_data.get("ID"),
            "variable2": bitrix_data.get("TITLE"),
            "variable3": f"Lead from Bitrix24: {bitrix_data.get('TITLE')}"
        }
    }

    headers = {
        "Authorization": f"Bearer {BOLNA_TOKEN}",
        "Content-Type": "application/json"
    }

    response = requests.post("https://api.bolna.ai/call", json=payload, headers=headers)

    return {
        "status": "forwarded",
        "bolna_response": response.text
    }

@app.get("/health")
def health_check():
    return {"status": "ok"}