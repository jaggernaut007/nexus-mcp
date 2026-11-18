from fastapi import APIRouter

from app import services

router = APIRouter(prefix="/orders")


@router.get("")
def list_orders():
    return services.all_orders()


@router.get("/{order_id}")
def get_order(order_id: int):
    return services.find_order(order_id)


@router.post("")
def create_order(payload: dict):
    return services.save_order(payload)


@router.delete("/{order_id}")
def cancel_order(order_id: int):
    return services.remove_order(order_id)
