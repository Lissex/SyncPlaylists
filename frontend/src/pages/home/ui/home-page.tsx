import { useQuery } from "@tanstack/react-query";

import { api } from "@/shared/api";
import { GroupedList, GroupedRow } from "@/shared/ui/grouped-list";
import { Logo } from "@/shared/ui/logo";

// Заглушка каркаса (5-1): проверяет, что /api доходит до бэкенда. Экраны — с 5-2.
export function HomePage() {
  const health = useQuery({
    queryKey: ["health"],
    queryFn: () => api.get<{ status: string }>("/health"),
  });
  const status = health.isPending
    ? "Проверяем…"
    : health.isError
      ? health.error.message
      : "На связи";

  return (
    <main className="mx-auto max-w-lg space-y-8 px-4 pt-[max(env(safe-area-inset-top),2rem)] pb-10">
      <Logo />
      <h1 className="text-[34px] leading-tight font-bold tracking-tight">Перенос плейлистов</h1>
      <GroupedList header="Сервер" footer="Экраны появятся на следующих подэтапах.">
        <GroupedRow
          title="API"
          trailing={<span className={health.isError ? "text-danger" : undefined}>{status}</span>}
        />
      </GroupedList>
    </main>
  );
}
