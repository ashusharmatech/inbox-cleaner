export const API = import.meta.env.VITE_API_URL || "http://localhost:8000";

const KEY = "ic_token";
export const getToken = () => localStorage.getItem(KEY);
export const setToken = (t) => localStorage.setItem(KEY, t);
export const clearToken = () => localStorage.removeItem(KEY);

export class AuthError extends Error {}

async function call(path, method = "GET") {
  const res = await fetch(API + path, {
    method,
    headers: { Authorization: `Bearer ${getToken()}` },
  });
  if (res.status === 401) throw new AuthError("Session expired");
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `Request failed (${res.status})`);
  return res.json();
}

export const api = {
  me: () => call("/me"),
  scan: () => call("/scan", "POST"),
  senders: () => call("/senders"),
  unsubscribe: (id) => call(`/senders/${id}/unsubscribe`, "POST"),
  remove: (id) => call(`/senders/${id}/delete`, "POST"),
  keep: (id) => call(`/senders/${id}/keep`, "POST"),
  deleteAccount: () => call("/account", "DELETE"),
};
