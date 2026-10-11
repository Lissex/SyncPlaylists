import { infiniteQueryOptions, queryOptions } from "@tanstack/react-query";

import { api, type Schemas } from "@/shared/api";

import { transferKeys, type Transfer, type TransferItem } from "./transfer";

type ItemsPage = Schemas["TransferItemsPage"];
type HistoryPage = Schemas["TransferHistoryPage"];

export type ItemStatus = TransferItem["status"];

/** Шапка переноса без треков: статус, счётчики, ETA, методы сопоставления. */
export const transferQuery = (id: string) =>
  queryOptions({
    queryKey: transferKeys.detail(id),
    queryFn: () => api.get<Transfer>(`/transfers/${id}?include_items=false`),
  });

/** Треки переноса страницами по 50 — ревью (сомнительные, ненайденные) и отчёт. */
export const transferItemsQuery = (id: string, statuses: readonly string[]) =>
  infiniteQueryOptions({
    queryKey: transferKeys.items(id, statuses),
    queryFn: ({ pageParam }) => {
      const params = new URLSearchParams(statuses.map((s) => ["status", s]));
      params.set("limit", "50");
      if (pageParam !== null) params.set("after", String(pageParam));
      return api.get<ItemsPage>(`/transfers/${id}/items?${params}`);
    },
    initialPageParam: null as number | null,
    getNextPageParam: (page) => page.next_after,
    // Решения на ревью обновляют кэш сами (без перезапроса: иначе решённый трек исчез бы
    // из списка «сомнительных» и его нельзя было бы пересмотреть).
    staleTime: Infinity,
  });

export const historyQuery = infiniteQueryOptions({
  queryKey: transferKeys.history,
  queryFn: ({ pageParam }) =>
    api.get<HistoryPage>(
      `/transfers?limit=20${pageParam ? `&cursor=${encodeURIComponent(pageParam)}` : ""}`,
    ),
  initialPageParam: null as string | null,
  getNextPageParam: (page) => page.next_cursor,
});
