#bolna_proxy
from fastapi import Request,APIRouter
import requests
router = APIRouter()
from urllib.parse import parse_qs
from datetime import datetime
from config import BITRIX_WEBHOOK, BOLNA_TOKEN, supabase
from helpers.retry_manager import insert_or_increment_retry


# ---------- Bitrix Lead → Bolna trigger (unchanged) ----------

@router.post("/bolna-proxy")
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


    lead_first_name = lead_data.get("NAME")

    if not phone:
        return {"status": "skipped", "reason": "No phone number found"}

    lead_name_l = (lead_name or "").lower()
    lead_fname_l = (lead_first_name or "").lower()

    if not (
        "swciad_" in lead_name_l
        or "ilts_" in lead_name_l
        or "assoma_" in lead_name_l   # ✅ ADD THIS
        or "udipth" in lead_name_l
        or "udipth" in lead_fname_l
    ):
        return {
            "status": "skipped",
            "reason": "Lead not eligible for auto-calling",
        }


    insert_or_increment_retry(
    lead_id=lead_data.get("ID"),
    phone=phone,
    lead_name=lead_name,
    lead_first_name=lead_first_name,
    reason="bitrix_webhook"
    )

    return {
        "status": "queued",
        "lead_id": lead_data.get("ID")
    }



