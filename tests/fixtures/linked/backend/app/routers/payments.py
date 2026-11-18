from fastapi import APIRouter

from app import services

router = APIRouter(prefix="/payments")


@router.post("/charge")
def charge_card(payload: dict):
    return services.charge(payload["order"], payload["card"])


@router.get("/{payment_id}")
def get_payment(payment_id: int):
    return services.find_payment(payment_id)
