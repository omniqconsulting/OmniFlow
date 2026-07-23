import { apiRequest } from "./client";

export type KnowledgeMediaKind = "document" | "video" | "audio" | "image" | "link";

export type KnowledgeItem = {
  id: string;
  title: string;
  description: string | null;
  category: string | null;
  media_kind: KnowledgeMediaKind;
  file_url: string | null;
  external_url: string | null;
  created_at: string;
};

type Page<T> = { items: T[]; next_cursor: string | null };

export function listKnowledge(params: { category?: string; cursor?: string; limit?: number } = {}): Promise<Page<KnowledgeItem>> {
  const q = new URLSearchParams();
  q.set("limit", String(params.limit ?? 50));
  if (params.category) q.set("category", params.category);
  if (params.cursor) q.set("cursor", params.cursor);
  return apiRequest<Page<KnowledgeItem>>(`/api/v1/knowledge?${q.toString()}`);
}
