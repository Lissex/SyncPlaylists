import { useInfiniteQuery } from "@tanstack/react-query";
import { useState } from "react";

import { platformInfo, type Platform } from "@/entities/platform";
import { transferItemsQuery, type Transfer } from "@/entities/transfer";
import { StartWritingButton } from "@/features/start-writing";
import { readStored, writeStored } from "@/shared/lib/storage";
import { useIsTouchDevice } from "@/shared/lib/use-media-query";
import { Button } from "@/shared/ui/button";
import { SegmentedControl } from "@/shared/ui/segmented-control";
import { Spinner } from "@/shared/ui/spinner";

import { ReviewItem, type ReviewView } from "./review-item";

const REVIEW_STATUSES = ["uncertain", "not_found"] as const;
const VIEW_KEY = "sp-review-view";
const VIEWS = [
  { value: "list", label: "Список" },
  { value: "carousel", label: "Карусель обложек" },
] as const;

/**
 * Ревью: сомнительные и ненайденные треки с кандидатами. По умолчанию — список (всё
 * видно сразу); на телефоне — карусель обложек (листать свайпом), на десктопе карусель
 * включается переключателем. Запись — только кнопкой «Записать».
 */
export function ReviewPanel({ transfer }: { transfer: Transfer }) {
  const touch = useIsTouchDevice();
  const [stored, setStored] = useState(() => readStored(VIEW_KEY));
  const view: ReviewView = touch
    ? "carousel"
    : stored === "carousel" || stored === "list"
      ? stored
      : "list";
  // Запрос — на исходные статусы ревью: решённые треки остаются в кэше с отметкой.
  const items = useInfiniteQuery(transferItemsQuery(transfer.id, REVIEW_STATUSES));
  const list = items.data?.pages.flatMap((page) => page.items) ?? [];
  const destination = platformInfo(transfer.destination.platform as Platform);
  const p = transfer.progress;

  return (
    <section className="space-y-4" aria-label="Проверка треков">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-[22px] font-bold">Проверьте треки</h2>
          <p className="text-[15px] text-label-secondary">
            {p ? `Сомнительных: ${p.uncertain} · не найдено: ${p.not_found}` : null}
          </p>
        </div>
        {!touch && (
          <div className="w-64">
            <SegmentedControl
              label="Вид"
              value={view}
              options={VIEWS}
              onChange={(value) => {
                setStored(value);
                writeStored(VIEW_KEY, value);
              }}
            />
          </div>
        )}
      </div>

      {items.isPending ? (
        <div className="flex justify-center py-8">
          <Spinner />
        </div>
      ) : items.isError ? (
        <p role="alert" className="text-danger">
          {items.error.message}
        </p>
      ) : (
        <div className="space-y-4">
          {list.map((item) => (
            <ReviewItem key={item.position} transferId={transfer.id} item={item} view={view} />
          ))}
          {items.hasNextPage && (
            <Button
              variant="secondary"
              size="block"
              disabled={items.isFetchingNextPage}
              onClick={() => void items.fetchNextPage()}
            >
              Показать ещё
            </Button>
          )}
        </div>
      )}

      <div className="sticky bottom-[calc(env(safe-area-inset-bottom)+4.5rem)] rounded-group bg-background/90 py-3 backdrop-blur-xl md:bottom-4">
        <StartWritingButton
          transferId={transfer.id}
          platformName={destination.name}
          undecided={p?.uncertain ?? 0}
        />
      </div>
    </section>
  );
}
