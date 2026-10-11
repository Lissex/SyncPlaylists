import { useQuery } from "@tanstack/react-query";

import { transferKeys, type ResolvedLink } from "@/entities/transfer";
import { api } from "@/shared/api";
import { useDebounced } from "@/shared/lib/use-debounced";

/** Похоже ли на ссылку настолько, чтобы спрашивать сервер (текст «Поделиться» тоже подходит). */
export function looksLikeLink(text: string): boolean {
  return /https?:\/\/\S+\.\S+/.test(text) || /\b[\w-]+\.[a-z]{2,}\/\S+/i.test(text);
}

/** Предпросмотр ссылки: площадка, плейлист или медиатека, название, число треков. */
export function useResolvedLink(url: string) {
  const debounced = useDebounced(url.trim(), 500);
  const query = useQuery({
    queryKey: transferKeys.resolve(debounced),
    queryFn: () => api.post<ResolvedLink>("/links/resolve", { url: debounced }),
    enabled: looksLikeLink(debounced),
    staleTime: 5 * 60_000,
    retry: false,
  });
  // Ответ относится к тому, что сейчас в поле (а не к ссылке до правки) и без ошибки.
  const settled = debounced === url.trim() && !query.isFetching;
  return { ...query, ready: settled && query.isSuccess && !query.isError };
}

export type ResolvedLinkState = ReturnType<typeof useResolvedLink>;
