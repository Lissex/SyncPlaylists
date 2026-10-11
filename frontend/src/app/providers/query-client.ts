import { QueryCache, QueryClient } from "@tanstack/react-query";

import { ApiError } from "@/shared/api";

// 4xx не повторяем (ответ не изменится), сеть и 5xx — до двух повторов.
// 401 в любом запросе — сессия истекла: onUnauthorized уводит на вход.
export function createQueryClient(onUnauthorized: () => void = () => {}): QueryClient {
  return new QueryClient({
    queryCache: new QueryCache({
      onError: (error) => {
        if (error instanceof ApiError && error.status === 401) onUnauthorized();
      },
    }),
    defaultOptions: {
      queries: {
        staleTime: 10_000,
        refetchOnWindowFocus: false,
        retry: (failures, error) =>
          !(error instanceof ApiError && error.status >= 400 && error.status < 500) && failures < 2,
      },
      mutations: { retry: false },
    },
  });
}
