# routes/call_now_webhook.py
from fastapi import APIRouter, Request
import requests

from config import BITRIX_WEBHOOK
from helpers.retry_manager import insert_or_increment_retry

router = APIRouter()

@router.post("/bitrix/call-now")
async def bitrix_call_now(request: Request):
    # Bitrix sends FORM / QUERY params, not JSON
    data = dict(await request.form()) if request.headers.get("content-type", "").startswith("application/x-www-form-urlencoded") else dict(request.query_params)

    print("📥 Bitrix call-now payload:", data)

    lead_id = data.get("ID") or data.get("lead_id")
    if not lead_id:
        return {"status": "ignored", "reason": "no lead id"}

    # Fetch full lead from Bitrix
    res = requests.get(
        f"{BITRIX_WEBHOOK}crm.lead.get.json",
        params={"id": lead_id},
        timeout=10
    )
    lead = res.json().get("result", {})

    phones = lead.get("PHONE") or []
    if not phones:
        return {"status": "ignored", "reason": "no phone"}

    phone = phones[0]["VALUE"]
    lead_first_name = lead.get("NAME")
    lead_name = f"{lead.get('NAME','')} {lead.get('LAST_NAME','')}".strip()

    # ✅ Queue retry-based calling
    insert_or_increment_retry(
        lead_id=str(lead_id),
        phone=phone,
        lead_name=lead_name,
        lead_first_name=lead_first_name,
        reason="bitrix_call_now_stage"
    )

    return {
        "status": "queued",
        "lead_id": lead_id
    }
