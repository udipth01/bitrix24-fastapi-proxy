from fastapi import FastAPI
from routes.bolna_proxy import router as bolna_router
from routes.post_call_webhook import router as postcall_router
from routes.retry_calls import router as retry_router  
from routes.bitrix_activity_webhook import router as bitrix_activity_router


app = FastAPI()

app.include_router(bolna_router, prefix="")
app.include_router(postcall_router, prefix="")
app.include_router(retry_router, prefix="")
app.include_router(bitrix_activity_router, prefix="")

print("\n🔍 Registered routes:")
for route in app.routes:
    print("→", route.path)


@app.get("/health")
def health_check():
    return {"status": "ok"}
