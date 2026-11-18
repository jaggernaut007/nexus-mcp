import { expect, test } from "vitest";

test("orders route answers", async () => {
  const res = await fetch("/api/orders");
  expect(res.ok).toBe(true);
});
