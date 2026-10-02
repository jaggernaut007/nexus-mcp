export interface CartLine {
  sku: string;
  priceCents: number;
  quantity: number;
}

export function addToCart(cart: CartLine[], line: CartLine): CartLine[] {
  const existing = cart.find((l) => l.sku === line.sku);
  if (existing) {
    existing.quantity += line.quantity;
    return cart;
  }
  return [...cart, line];
}

export function cartTotal(cart: CartLine[]): number {
  return cart.reduce((sum, l) => sum + l.priceCents * l.quantity, 0);
}
