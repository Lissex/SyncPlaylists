import { createFileRoute, redirect } from "@tanstack/react-router";

import { sessionQuery } from "@/entities/session";
import { LoginPage } from "@/pages/login";

import { authSearchSchema } from "../lib/redirect";

export const Route = createFileRoute("/login")({
  validateSearch: authSearchSchema,
  // Уже вошёл — форма не нужна.
  beforeLoad: async ({ context, search }) => {
    if (await context.queryClient.ensureQueryData(sessionQuery)) {
      throw redirect({ to: search.redirect ?? "/" });
    }
  },
  component: function Page() {
    const { redirect: next } = Route.useSearch();
    return <LoginPage redirect={next} />;
  },
});
