import { queryOptions } from "@tanstack/react-query";

import { ApiError, api, type Schemas } from "@/shared/api";

export type User = Schemas["UserResponse"];

export const sessionKeys = {
  me: ["session", "me"] as const,
};

/** Текущий пользователь или null, если не вошёл (401 — не ошибка, а «гость»). */
export async function fetchCurrentUser(): Promise<User | null> {
  try {
    return await api.get<User>("/auth/me");
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) return null;
    throw error;
  }
}

export const sessionQuery = queryOptions({
  queryKey: sessionKeys.me,
  queryFn: fetchCurrentUser,
  staleTime: 5 * 60_000,
});
