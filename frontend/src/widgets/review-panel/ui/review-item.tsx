import { formatDuration, type TransferItem } from "@/entities/transfer";
import { useResolveItem } from "@/features/resolve-item";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";

import { CandidateCard, CandidateRow } from "./candidate";

export type ReviewView = "list" | "carousel";

function decision(item: TransferItem): "chosen" | "skipped" | null {
  if (item.status === "skipped") return "skipped";
  if (item.status === "matched" && item.match?.method === "manual") return "chosen";
  return null;
}

/** Один трек на ревью: что искали, кандидаты, «Выбрать» / «Пропустить». */
export function ReviewItem({
  transferId,
  item,
  view,
}: {
  transferId: string;
  item: TransferItem;
  view: ReviewView;
}) {
  const resolve = useResolveItem(transferId);
  const decided = decision(item);
  const sought = item.source_title
    ? `${item.source_artist ?? ""}${item.source_artist ? " — " : ""}${item.source_title}`
    : `Трек ${item.source_external_id}`;
  const soughtDuration = formatDuration(item.source_duration_ms);
  const isChosen = (c: TransferItem["candidates"][number]) =>
    decided === "chosen" &&
    item.match?.target_external_id === c.external_id &&
    item.match.target_platform === c.platform;
  const choose = (c: TransferItem["candidates"][number]) =>
    resolve.mutate({ item, choice: { platform: c.platform, externalId: c.external_id } });

  return (
    <article className="overflow-hidden rounded-group bg-surface" aria-label={sought}>
      <header className="flex items-start justify-between gap-3 px-4 pt-3 pb-2">
        <div className="min-w-0">
          <div className="text-[12px] text-label-tertiary uppercase">Искали</div>
          <div className="text-[17px] font-semibold">{sought}</div>
          {soughtDuration && (
            <div className="text-[13px] text-label-secondary">{soughtDuration}</div>
          )}
        </div>
        {decided === "chosen" ? (
          <Badge tone="brand">Выбрано</Badge>
        ) : decided === "skipped" ? (
          <Badge>Пропущено</Badge>
        ) : item.status === "uncertain" ? (
          <Badge tone="warning">Проверьте</Badge>
        ) : (
          <Badge tone="danger">Не найден</Badge>
        )}
      </header>

      {item.candidates.length === 0 ? (
        <p className="px-4 pb-3 text-[15px] text-label-secondary">
          На площадке назначения ничего похожего не нашлось.
        </p>
      ) : view === "carousel" ? (
        <div
          className="flex snap-x snap-mandatory [scrollbar-width:none] gap-3 overflow-x-auto px-4 pb-3"
          aria-label="Кандидаты — листайте вбок"
        >
          {item.candidates.map((c) => (
            <CandidateCard
              key={`${c.platform}:${c.external_id}`}
              candidate={c}
              sourceMs={item.source_duration_ms}
              chosen={isChosen(c)}
              disabled={resolve.isPending}
              onChoose={() => choose(c)}
            />
          ))}
        </div>
      ) : (
        <div className="divide-y-[0.5px] divide-separator border-t-[0.5px] border-separator">
          {item.candidates.map((c) => (
            <CandidateRow
              key={`${c.platform}:${c.external_id}`}
              candidate={c}
              sourceMs={item.source_duration_ms}
              chosen={isChosen(c)}
              disabled={resolve.isPending}
              onChoose={() => choose(c)}
            />
          ))}
        </div>
      )}

      <footer className="flex items-center justify-between gap-3 border-t-[0.5px] border-separator px-4 py-2">
        {resolve.isError ? (
          <span role="alert" className="text-[13px] text-danger">
            {resolve.error.message}
          </span>
        ) : (
          <span />
        )}
        {decided !== "skipped" && (
          <Button
            variant="plain"
            size="sm"
            className="text-label-secondary"
            disabled={resolve.isPending}
            onClick={() => resolve.mutate({ item, choice: null })}
          >
            Пропустить
          </Button>
        )}
      </footer>
    </article>
  );
}
