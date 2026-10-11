import { QueryClient } from "@tanstack/react-query";

import { ApiError } from "@/shared/api";

// 4xx не повторяем (ответ не изменится), сеть и 5xx — до двух повторов.
export function createQueryClient(): QueryClient {
  return new QueryClient({
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
