import { useInfiniteQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ChevronRight } from "lucide-react";

import { RouteLine, sourceTitle, type Platform } from "@/entities/platform";
import {
  historyQuery,
  isFinished,
  isPaused,
  statusLabel,
  type TransferSummary,
} from "@/entities/transfer";
import { timeAgo } from "@/shared/lib/time";
import { Button } from "@/shared/ui/button";
import { Spinner } from "@/shared/ui/spinner";
import { StatusText, type Tone } from "@/shared/ui/status-text";

function tone(summary: TransferSummary): Tone {
  if (summary.status === "done") return "ok";
  if (summary.status === "failed") return "danger";
  if (summary.status === "review" || isPaused(summary.status)) return "warning";
  return "muted";
}

function counters(s: TransferSummary): string {
  if (s.status === "done") return `перенесено ${s.added} из ${s.total}`;
  if (s.total === 0) return "читаем источник";
  return `найдено ${s.matched + s.added} из ${s.total}`;
}

/** История переносов: новые сверху; пока есть незавершённые — обновляется сама. */
export function HistoryList() {
  const history = useInfiniteQuery({
    ...historyQuery,
    refetchInterval: (query) =>
      query.state.data?.pages.some((page) => page.items.some((s) => !isFinished(s.status)))
        ? 10_000
        : false,
  });
  const items = history.data?.pages.flatMap((page) => page.items) ?? [];

  if (history.isPending) {
    return (
      <div className="flex justify-center py-10">
        <Spinner />
      </div>
    );
  }
  if (history.isError) {
    return (
      <p role="alert" className="text-danger">
        {history.error.message}
      </p>
    );
  }
  if (items.length === 0) {
    return (
      <div className="space-y-4 rounded-group bg-surface px-4 py-6 text-center">
        <p className="text-[17px]">Переносов пока нет</p>
        <p className="text-[15px] text-label-secondary">
          Вставьте ссылку на плейлист — найдём треки на другой площадке.
        </p>
        <Link to="/" className="inline-block text-[17px] font-semibold text-brand">
          Новый перенос
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <ul className="divide-y-[0.5px] divide-separator overflow-hidden rounded-group bg-surface">
        {items.map((summary) => (
          <li key={summary.id}>
            <Link
              to="/transfers/$transferId"
              params={{ transferId: summary.id }}
              className="flex items-center gap-3 px-4 py-3 transition hover:bg-surface-raised/50"
            >
              <div className="min-w-0 flex-1 space-y-1">
                <div className="flex items-baseline justify-between gap-3">
                  <span className="min-w-0 truncate text-[17px] font-medium">
                    {sourceTitle(
                      summary.source_title,
                      summary.source.kind,
                      summary.source.platform as Platform,
                    )}
                  </span>
                  <span className="shrink-0 text-[13px] whitespace-nowrap text-label-tertiary">
                    {timeAgo(summary.created_at)}
                  </span>
                </div>
                <RouteLine
                  from={summary.source.platform as Platform}
                  to={summary.destination.platform as Platform}
                  destinationKind={summary.destination.kind}
                />
                <div className="flex flex-wrap items-center gap-x-3 text-[13px] text-label-secondary">
                  <StatusText tone={tone(summary)}>{statusLabel(summary.status)}</StatusText>
                  <span>{counters(summary)}</span>
                </div>
              </div>
              <ChevronRight className="size-5 shrink-0 text-label-tertiary" aria-hidden="true" />
            </Link>
          </li>
        ))}
      </ul>
      {history.hasNextPage && (
        <Button
          variant="secondary"
          size="block"
          disabled={history.isFetchingNextPage}
          onClick={() => void history.fetchNextPage()}
        >
          Показать ещё
        </Button>
      )}
    </div>
  );
}
