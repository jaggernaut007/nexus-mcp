from shop.pricing import apply_discount, calculate_total


def test_apply_discount_known_code():
    assert apply_discount(1000, "WELCOME10") == 900


def test_calculate_total_adds_tax():
    assert calculate_total([(1000, 1)], "US") == 1070
