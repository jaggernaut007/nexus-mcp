from fastapi import APIRouter

from app import services

router = APIRouter(prefix="/users")


@router.get("/me")
def current_user():
    return {"id": 1, "name": "Ada"}


@router.get("/{user_id}/orders")
def user_orders(user_id: int):
    return services.all_orders()
