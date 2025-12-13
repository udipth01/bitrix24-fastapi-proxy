# routes/retry_calls.py
from fastapi import APIRouter, Request, Header, HTTPException
from helpers.retry_manager import process_due_retries
import os

router = APIRouter()

CRON_SECRET = os.getenv("CRON_SECRET")  # set this on Render, e.g., 'super-secret-cron-token'

@router.post("/cron/retry-calls")
async def cron_retry_calls(request: Request, x_cron_secret: str | None = Header(None)):
    # simple header auth
    if CRON_SECRET and x_cron_secret != CRON_SECRET:
        raise HTTPException(status_code=403, detail="Unauthorized")

    # process due retries
    results = process_due_retries()
    return {"status": "ok", "processed": len(results), "details": results}
