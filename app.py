from fastapi import FastAPI
from routes.bolna_proxy import router as bolna_router
from routes.post_call_webhook import router as postcall_router
from routes.retry_calls import router as retry_router  
from routes.bitrix_activity_webhook import router as bitrix_activity_router
from routes.call_now_webhook import router as call_now_router


app = FastAPI()

app.include_router(bolna_router, prefix="")
app.include_router(postcall_router, prefix="")
app.include_router(retry_router, prefix="")
app.include_router(bitrix_activity_router, prefix="")

app.include_router(call_now_router)

print("\n🔍 Registered routes:")
for route in app.routes:
    print("→", route.path)


@app.get("/health")
def health_check():
    return {"status": "ok"}
