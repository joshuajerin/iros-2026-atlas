import type { Network, Overview, Paper } from "./types";

async function request<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`Atlas request failed: ${response.status}`);
  return response.json() as Promise<T>;
}

export const api = {
  overview: () => request<Overview>("/api/v1/overview"),
  network: () => request<Network>("/api/v1/keywords/network?limit=64"),
  papers: (params: URLSearchParams) => request<{ count: number; papers: Paper[] }>(`/api/v1/papers?${params}`),
  paper: (id: string) => request<Paper>(`/api/v1/papers/${id}`),
  rankings: (kind: string, params = "") => request<{ items: RankingItem[]; methodology?: string }>(`/api/v1/rankings/${kind}?${params}`),
  topic: (slug: string) => request<{ slug: string; label: string; paper_count: number; keywords: { slug: string; label: string; paper_count: number }[]; rankings: Paper[] }>(`/api/v1/topics/${slug}`),
  methodology: () => request<{ version: string; computed_at: string; weights: Record<string, number>; rules: string[] }>("/api/v1/methodology/atlas-score"),
};

export type RankingItem = { id?: string | number; name?: string; paper_count?: number; score?: number; topic_breadth?: number; top_topics?: string[]; title?: string; paper_number?: string; authors?: string[]; topics?: string[]; confidence?: number; eligible?: boolean };
