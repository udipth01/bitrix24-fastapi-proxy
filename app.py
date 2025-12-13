from fastapi import FastAPI
from routes.bolna_proxy import router as bolna_router
from routes.post_call_webhook import router as postcall_router
from routes.retry_calls import router as retry_router  
from routes.bitrix_activity_webhook import router as bitrix_activity_router


app = FastAPI()

app.include_router(bolna_router)
app.include_router(postcall_router)
app.include_router(retry_router)
app.include_router(bitrix_activity_router)

@app.get("/health")
def health_check():
    return {"status": "ok"}
