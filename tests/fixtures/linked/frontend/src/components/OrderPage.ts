import { chargeCard, fetchOrder } from "../api/client";

export async function openOrder(id: string) {
  const order = await fetchOrder(id);
  return order;
}

export async function payForOrder(body: object) {
  return chargeCard(body);
}
