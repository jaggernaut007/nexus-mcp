import axios from "axios";

const BASE = import.meta.env.VITE_API_URL;

export async function listOrders() {
  const res = await fetch(`${BASE}/api/orders`);
  return res.json();
}

export async function fetchOrder(id: string) {
  const res = await fetch(`${BASE}/api/orders/${id}`);
  return res.json();
}

export function createOrder(body: object) {
  return axios.post("/api/orders", body);
}

export function cancelOrder(id: string) {
  return axios.delete(`/api/orders/${id}`);
}

export function chargeCard(body: object) {
  return axios.post("/api/payments/charge", body);
}

export function loadPayment(id: string) {
  return axios.get(`/api/payments/${id}`);
}

export async function currentUser() {
  return fetch("/api/users/me");
}

export async function ordersOfUser(userId: string) {
  return fetch(`/api/users/${userId}/orders`);
}

export async function checkHealth() {
  return fetch("/api/health");
}

// A true call that the extractor cannot see: the path is in a table, not in the call.
const ENDPOINTS = { orders: "/api/orders" };

export function listOrdersFromTable() {
  return axios.get(ENDPOINTS.orders);
}

// Decoys: neither call reaches a route of the backend.
export async function trackShipment(id: string) {
  return fetch(`https://tracking.example.com/v2/track/${id}`);
}

export async function loadInvoice(id: string) {
  return fetch(`/api/invoices/${id}`);
}
