// REST client for the FastAPI backend. Same-origin fetches (dev: proxied by Vite,
// see vite.config.ts; prod: served by the same FastAPI app that mounts /web).
import type { BusinessOut, ChatRequest, ChatResponse, ConversationDetail, ConversationSummary } from "./types";

async function getJSON<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error("bad status");
  return (await res.json()) as T;
}

export function getBusinesses(): Promise<BusinessOut[]> {
  return getJSON<BusinessOut[]>("/businesses");
}

export function getConversations(businessId: string, limit = 50): Promise<ConversationSummary[]> {
  return getJSON<ConversationSummary[]>(`/businesses/${businessId}/conversations?limit=${limit}`);
}

export function getConversationDetail(businessId: string, conversationId: string): Promise<ConversationDetail> {
  return getJSON<ConversationDetail>(`/businesses/${businessId}/conversations/${conversationId}`);
}

export async function postChat(payload: ChatRequest): Promise<ChatResponse> {
  const res = await fetch("/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`);
  return (await res.json()) as ChatResponse;
}
