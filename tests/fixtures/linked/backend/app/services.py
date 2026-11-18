ORDERS = {1: {"id": 1, "total": 12}}
PAYMENTS = {}


def all_orders():
    return list(ORDERS.values())


def find_order(order_id):
    return ORDERS.get(order_id)


def save_order(payload):
    ORDERS[len(ORDERS) + 1] = payload
    return payload


def remove_order(order_id):
    return ORDERS.pop(order_id, None)


def charge(order_id, card):
    PAYMENTS[order_id] = card
    return {"order": order_id, "paid": True}


def find_payment(payment_id):
    return PAYMENTS.get(payment_id)
