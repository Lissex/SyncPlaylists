import { CircleAlert, PauseCircle } from "lucide-react";

import { RouteLine, sourceTitle, type Platform } from "@/entities/platform";
import {
  currentStep,
  formatEta,
  isFinished,
  STEPS,
  type Transfer,
  type TransferStream,
} from "@/entities/transfer";
import { cn } from "@/shared/lib/cn";
import { countOf, TRACKS } from "@/shared/lib/plural";
import { GroupedList, GroupedRow } from "@/shared/ui/grouped-list";

import { pauseInfo } from "../lib/pause";

function Stepper({ transfer }: { transfer: Transfer }) {
  const p = transfer.progress;
  const step = currentStep(transfer.status, p?.total ?? 0, p?.pending ?? 0);
  const index = STEPS.findIndex((s) => s.id === step);
  return (
    <ol className="flex gap-1" aria-label="Этапы переноса">
      {STEPS.map((s, i) => (
        <li
          key={s.id}
          className="flex-1 space-y-1.5"
          aria-current={i === index ? "step" : undefined}
        >
          <div
            className={cn(
              "h-1 rounded-full",
              i < index || transfer.status === "done"
                ? "bg-brand"
                : i === index
                  ? "bg-brand/50"
                  : "bg-surface-raised",
            )}
          />
          <div
            className={cn(
              "text-[11px]",
              i === index ? "font-semibold text-label" : "text-label-tertiary",
            )}
          >
            {s.label}
          </div>
        </li>
      ))}
    </ol>
  );
}

function progressLine(transfer: Transfer): { percent: number; text: string } {
  const p = transfer.progress;
  if (!p || p.total === 0) return { percent: 0, text: "Читаем источник…" };
  if (transfer.status === "writing" || transfer.status === "done") {
    const toWrite = Math.max(1, p.total - p.not_found - p.skipped);
    const written = p.added + p.failed;
    return {
      percent: Math.min(100, (written / toWrite) * 100),
      text: `Записано ${written} из ${countOf(toWrite, TRACKS)}`,
    };
  }
  const processed = p.total - p.pending;
  const eta = p.eta_seconds != null && p.pending > 0 ? ` · ещё ${formatEta(p.eta_seconds)}` : "";
  return {
    percent: (processed / p.total) * 100,
    text: `${processed} из ${countOf(p.total, TRACKS)}${eta}`,
  };
}

export function TransferProgressView({
  transfer,
  stream,
}: {
  transfer: Transfer;
  stream: TransferStream;
}) {
  const p = transfer.progress;
  const pause = pauseInfo(transfer);
  const line = progressLine(transfer);
  const finished = isFinished(transfer.status);

  return (
    <div className="space-y-6">
      <header className="space-y-1.5">
        <h1 className="text-[28px] leading-tight font-bold tracking-tight">
          {sourceTitle(
            transfer.source_title,
            transfer.source.kind,
            transfer.source.platform as Platform,
          )}
        </h1>
        <RouteLine
          from={transfer.source.platform as Platform}
          to={transfer.destination.platform as Platform}
          destinationKind={transfer.destination.kind}
        />
      </header>

      {transfer.status !== "failed" && <Stepper transfer={transfer} />}

      {!finished && (
        <div className="space-y-2">
          <div
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(line.percent)}
            className="h-1.5 overflow-hidden rounded-full bg-surface-raised"
          >
            <div
              className="h-full rounded-full bg-brand transition-[width] duration-500 ease-ios"
              style={{ width: `${line.percent}%` }}
            />
          </div>
          <div className="flex justify-between text-[13px] text-label-secondary">
            <span>{line.text}</span>
            {!stream.live && <span>переподключаемся…</span>}
          </div>
        </div>
      )}

      {pause && (
        <div role="status" className="flex gap-3 rounded-group bg-surface px-4 py-3">
          <span
            className={cn(
              "inline-flex size-8 shrink-0 items-center justify-center rounded-[8px]",
              pause.tone === "warning"
                ? "bg-warning text-black"
                : "bg-surface-raised text-label-secondary",
            )}
          >
            <PauseCircle className="size-5" aria-hidden="true" />
          </span>
          <div>
            <div className="text-[17px] font-semibold">{pause.title}</div>
            <p className="text-[15px] text-label-secondary">{pause.text}</p>
          </div>
        </div>
      )}

      {transfer.status === "failed" && (
        <div role="alert" className="flex gap-3 rounded-group bg-surface px-4 py-3">
          <span className="inline-flex size-8 shrink-0 items-center justify-center rounded-[8px] bg-danger text-white">
            <CircleAlert className="size-5" aria-hidden="true" />
          </span>
          <div>
            <div className="text-[17px] font-semibold">Перенос остановился</div>
            <p className="text-[15px] text-label-secondary">{failureText(stream.failureReason)}</p>
          </div>
        </div>
      )}

      {p && p.total > 0 && !finished && (
        <GroupedList>
          <GroupedRow title="Найдено" trailing={p.matched + p.added} />
          {p.uncertain > 0 && (
            <GroupedRow
              title="Нужно проверить"
              trailing={<span className="text-warning">{p.uncertain}</span>}
            />
          )}
          {p.recognizing > 0 && <GroupedRow title="Распознаём по звуку" trailing={p.recognizing} />}
          {p.not_found > 0 && <GroupedRow title="Не найдено" trailing={p.not_found} />}
          {p.skipped > 0 && <GroupedRow title="Пропущено" trailing={p.skipped} />}
          {p.added > 0 && <GroupedRow title="Записано" trailing={p.added} />}
          {p.failed > 0 && (
            <GroupedRow title="Ошибки" trailing={<span className="text-danger">{p.failed}</span>} />
          )}
        </GroupedList>
      )}
    </div>
  );
}

const FAILURES: Record<string, string> = {
  account_unavailable:
    "Аккаунт площадки отключён. Подключите его снова и запустите перенос заново.",
  account_expired: "Доступ к площадке истёк. Переподключите её и запустите перенос заново.",
  playlist_not_found: "Плейлист не найден или стал закрытым.",
  playlist_not_writable: "В выбранный плейлист нельзя добавлять — он принадлежит другому человеку.",
  region_blocked: "Площадка недоступна из региона сервера.",
  write_requires_extension: "Записывать на эту площадку можно только через расширение.",
  platform_not_supported: "Площадка пока не поддерживается.",
};

function failureText(reason: string | null): string {
  return (
    (reason && FAILURES[reason]) ??
    "Что-то пошло не так. Запустите перенос заново — уже найденное найдётся быстрее."
  );
}
