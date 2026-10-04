// Types mirroring the Pydantic response models in app/api/*.py exactly (field names,
// optionality). Keep in sync by hand -- no codegen step in stage 1.

// app/api/businesses.py
export interface LocationOut {
  id: string;
  name: string | null;
  url: string | null;
}

export interface BusinessOut {
  id: string;
  name: string;
  website: string | null;
  domain: string | null;
  conversation_count: number;
  default_location: LocationOut | null;
}

// app/api/conversations.py
export interface ConversationSummary {
  id: string;
  channel: string;
  title: string;
  preview: string;
  started_at: string; // ISO datetime
  duration_s: number;
  message_count: number;
}

export interface MessageOut {
  role: string;
  content: string;
  at_s: number;
}

export interface ConversationDetail {
  id: string;
  channel: string;
  title: string;
  started_at: string; // ISO datetime
  duration_s: number;
  messages: MessageOut[];
}

// app/api/chat.py
export interface ChatSource {
  chunk_id: string;
  section_heading: string | null;
  score: number;
  source: string;
}

export interface ChatRequest {
  business_id: string;
  question: string;
  conversation_id: string | null;
}

export interface ChatResponse {
  answer: string;
  conversation_id: string;
  sources: ChatSource[];
}
