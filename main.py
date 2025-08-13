from fastapi import FastAPI, Request
import requests
import os
token = os.getenv("BOLNA_API_KEY")
app = FastAPI()

@app.post("/bolna-proxy")
async def bolna_proxy(request: Request):
    bitrix_data = await request.json()

    payload = {
        "agent_id": "950c36e8-92ed-4a21-9764-03267e2f6039",
        "recipient_phone_number": bitrix_data.get("phone"),
        "from_phone_number": "+918035316588",  # Your registered number
        "user_data": {
            "variable1": bitrix_data.get("lead_id"),
            "variable2": bitrix_data.get("name"),
            "variable3": f"Lead from Bitrix24: {bitrix_data.get('name')}"
        }
    }

    headers = {
        "Authorization": "Bearer <your_bolna_token>",
        "Content-Type": "application/json"
    }

    response = requests.post("https://api.bolna.ai/call", json=payload, headers=headers)
    return {"status": "forwarded", "bolna_response": response.text}

@app.get("/health")
def health_check():
    return {"status": "ok"}