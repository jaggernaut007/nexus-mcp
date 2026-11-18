from fastapi import FastAPI

from app.routers import orders, payments, users

app = FastAPI(title="Bookshop API")
app.include_router(orders.router, prefix="/api")
app.include_router(payments.router, prefix="/api")
app.include_router(users.router, prefix="/api")


@app.get("/api/health")
def health():
    return {"ok": True}
