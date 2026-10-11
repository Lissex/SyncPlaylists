import "./app/styles/index.css";

import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { createQueryClient } from "./app/providers/query-client";
import { createAppRouter } from "./app/router";
import { sessionKeys } from "./entities/session";
import { applyAccent, applyTheme, getAccent, getThemePreference } from "./shared/lib/theme";

applyTheme(getThemePreference());
applyAccent(getAccent());

// Сессия истекла посреди работы: забываем пользователя и ведём на вход с возвратом назад.
const queryClient = createQueryClient(() => {
  queryClient.setQueryData(sessionKeys.me, null);
  const { pathname, search } = window.location;
  if (pathname !== "/login") {
    void router.navigate({ to: "/login", search: { redirect: pathname + search } });
  }
});
const router = createAppRouter(queryClient);

const root = document.getElementById("root");
if (!root) throw new Error("Нет #root в index.html");

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
