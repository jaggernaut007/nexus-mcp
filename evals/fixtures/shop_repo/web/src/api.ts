import { CartLine, cartTotal } from "./cart";

export async function submitOrder(cart: CartLine[], code: string): Promise<string> {
  const response = await fetch("/orders", {
    method: "POST",
    body: JSON.stringify({ lines: cart, code, expected: cartTotal(cart) }),
  });
  if (!response.ok) {
    throw new Error(`order failed: ${response.status}`);
  }
  return (await response.json()).orderId;
}
