# routes/retry_calls.py
from fastapi import APIRouter, Request, Header, HTTPException
from helpers.retry_manager import process_due_retries,process_call_now_leads
import os

router = APIRouter()

CRON_SECRET = os.getenv("CRON_SECRET")  # set this on Render, e.g., 'super-secret-cron-token'

@router.post("/cron/retry-calls")
async def cron_retry_calls(request: Request, x_cron_secret: str | None = Header(None)):
    # simple header auth
    if CRON_SECRET and x_cron_secret != CRON_SECRET:
        raise HTTPException(status_code=403, detail="Unauthorized")
    
    call_now_count = 0
    retry_count = 0

    try:
        call_now = process_call_now_leads(limit=50)
        call_now_count = len(call_now)
    except Exception as e:
        print("❌ Error in process_call_now_leads:", e)

    try:
        results = process_due_retries()
        retry_count = len(results)
    except Exception as e:
        print("❌ Error in process_due_retries:", e)

    return {
        "status": "ok",
        "call_now_processed": call_now_count,
        "retry_calls_processed": retry_count,
    }
