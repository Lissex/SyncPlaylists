import { useMutation, useQueryClient } from "@tanstack/react-query";

import { sessionKeys, type User } from "@/entities/session";
import { api } from "@/shared/api";

export type Credentials = { email: string; password: string };

/** Вход и регистрация: сервер ставит HttpOnly-cookie сессии, ответ — пользователь. */
export function useAuthMutation(mode: "login" | "register") {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (credentials: Credentials) =>
      api.post<User>(mode === "login" ? "/auth/login" : "/auth/register", credentials),
    onSuccess: (user) => queryClient.setQueryData(sessionKeys.me, user),
  });
}
