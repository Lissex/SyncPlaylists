import { createFileRoute, Outlet, redirect } from "@tanstack/react-router";

import { sessionQuery } from "@/entities/session";
import { AppShell } from "@/widgets/app-shell";

// Всё приложение, кроме входа и регистрации, — только для вошедших.
export const Route = createFileRoute("/_app")({
  beforeLoad: async ({ context, location }) => {
    const user = await context.queryClient.ensureQueryData(sessionQuery);
    if (!user) {
      throw redirect({ to: "/login", search: { redirect: location.href } });
    }
    return { user };
  },
  component: () => (
    <AppShell>
      <Outlet />
    </AppShell>
  ),
});
