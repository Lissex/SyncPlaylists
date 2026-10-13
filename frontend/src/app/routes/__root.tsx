import { createRootRouteWithContext, Outlet } from "@tanstack/react-router";

import type { RouterContext } from "../router";

export const Route = createRootRouteWithContext<RouterContext>()({
  component: () => <Outlet />,
  notFoundComponent: () => (
    <main className="mx-auto max-w-lg px-4 py-16 text-center">
      <h1 className="text-[28px] font-bold">Страница не найдена</h1>
      <p className="mt-2 text-label-secondary">Проверьте адрес или вернитесь на главную.</p>
    </main>
  ),
});
