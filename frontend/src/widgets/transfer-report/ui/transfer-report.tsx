import { useInfiniteQuery } from "@tanstack/react-query";
import { CircleCheck } from "lucide-react";

import { platformInfo, type Platform } from "@/entities/platform";
import { methodLabel, transferItemsQuery, type Transfer } from "@/entities/transfer";
import { countOf, TRACKS } from "@/shared/lib/plural";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import { GroupedList, GroupedRow } from "@/shared/ui/grouped-list";

const LEFT_OUT = ["not_found", "skipped", "failed"] as const;
const LEFT_OUT_LABEL: Record<string, { text: string; tone: "danger" | "neutral" }> = {
  not_found: { text: "Не найден", tone: "danger" },
  skipped: { text: "Пропущен", tone: "neutral" },
  failed: { text: "Не записался", tone: "danger" },
};

/** Итог переноса: сколько перенесено, как нашли, что осталось за бортом. */
export function TransferReport({ transfer }: { transfer: Transfer }) {
  const p = transfer.progress;
  const destination = platformInfo(transfer.destination.platform as Platform);
  const leftOut = (p?.not_found ?? 0) + (p?.skipped ?? 0) + (p?.failed ?? 0);
  const items = useInfiniteQuery({
    ...transferItemsQuery(transfer.id, LEFT_OUT),
    enabled: leftOut > 0,
  });
  const methods = Object.entries(transfer.match_methods ?? {}).sort((a, b) => b[1] - a[1]);
  const parts = transfer.resolved_targets?.length ?? 0;

  return (
    <div className="space-y-8">
      {transfer.status === "done" && (
        <div className="flex items-center gap-3 rounded-group bg-surface px-4 py-3">
          <CircleCheck className="size-8 shrink-0 text-success" aria-hidden="true" />
          <div>
            <div className="text-[17px] font-semibold">
              Перенесено {countOf(p?.added ?? 0, TRACKS)} в {destination.name}
            </div>
            {transfer.destination.kind === "new" && parts > 0 && (
              <p className="text-[15px] text-label-secondary">
                {parts === 1
                  ? `Создан плейлист «${transfer.destination.title}».`
                  : `Создано ${parts} плейлиста «${transfer.destination.title} (1/${parts})»… — в один все треки не помещаются.`}
              </p>
            )}
          </div>
        </div>
      )}

      {p && (
        <GroupedList header="Итог">
          <GroupedRow title="Перенесено" trailing={p.added} />
          <GroupedRow title="Не найдено" trailing={p.not_found} />
          <GroupedRow title="Пропущено вами" trailing={p.skipped} />
          {p.failed > 0 && (
            <GroupedRow
              title="Не записались"
              trailing={<span className="text-danger">{p.failed}</span>}
            />
          )}
          <GroupedRow title="Всего в источнике" trailing={p.total} />
        </GroupedList>
      )}

      {methods.length > 0 && (
        <GroupedList header="Как нашли">
          {methods.map(([method, count]) => (
            <GroupedRow key={method} title={methodLabel(method)} trailing={count} />
          ))}
        </GroupedList>
      )}

      {leftOut > 0 && (
        <GroupedList
          header="Не перенесено"
          footer="Эти треки можно найти и добавить на площадке вручную."
        >
          {items.data?.pages
            .flatMap((page) => page.items)
            .map((item) => {
              const label = LEFT_OUT_LABEL[item.status];
              return (
                <GroupedRow
                  key={item.position}
                  title={item.source_title ?? `Трек ${item.source_external_id}`}
                  subtitle={item.source_artist}
                  trailing={label && <Badge tone={label.tone}>{label.text}</Badge>}
                />
              );
            })}
          {items.hasNextPage && (
            <div className="px-4 py-2">
              <Button
                variant="plain"
                size="sm"
                disabled={items.isFetchingNextPage}
                onClick={() => void items.fetchNextPage()}
              >
                Показать ещё
              </Button>
            </div>
          )}
        </GroupedList>
      )}
    </div>
  );
}
