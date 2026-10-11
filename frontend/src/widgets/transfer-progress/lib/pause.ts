import { extensionReasonText } from "@/entities/extension";
import { platformInfo, type Platform } from "@/entities/platform";
import type { Transfer } from "@/entities/transfer";
import { clockTime } from "@/shared/lib/time";

export type PauseInfo = { title: string; text: string; tone: "warning" | "muted" };

/**
 * Почему перенос стоит и что делать. Площадка, о которой речь: до чтения источника —
 * источник, потом — назначение (поиск и запись идут там).
 */
export function pauseInfo(transfer: Transfer, now: Date = new Date()): PauseInfo | null {
  const progress = transfer.progress;
  if (!progress) return null;
  const source = transfer.source.platform as Platform;
  const destination = transfer.destination.platform as Platform;
  const platform = platformInfo(progress.total === 0 ? source : destination);

  switch (progress.status) {
    case "paused_client":
      return {
        title: "Ждём браузер",
        text: extensionReasonText(progress.pause_reason, platform.site),
        tone: "warning",
      };
    case "paused_quota": {
      const at = progress.resume_at ? clockTime(progress.resume_at) : null;
      const tomorrow =
        progress.resume_at && new Date(progress.resume_at).toDateString() !== now.toDateString();
      const when = at ? `${tomorrow ? "завтра в" : "в"} ${at}` : "немного позже";
      if (progress.pause_reason === "vk_library_daily_limit") {
        return {
          title: "Дневной лимит VK",
          text: `VK разрешает добавлять в «Мою музыку» ограниченное число треков в сутки. Продолжим сами ${when} — ничего делать не нужно.`,
          tone: "muted",
        };
      }
      if (progress.pause_reason === "vk_flood_control") {
        return {
          title: "VK попросил паузу",
          text: `Слишком частые действия — VK просит подождать. Продолжим сами ${when}.`,
          tone: "muted",
        };
      }
      return {
        title: `Лимит ${platform.name}`,
        text: `${platform.name} просит подождать. Продолжим сами ${when} — ничего делать не нужно.`,
        tone: "muted",
      };
    }
    case "paused_captcha":
      return {
        title: "Площадка просит проверку",
        text: `${platform.name} попросила подтвердить, что запросы делает человек. Продолжим, как только проверка пройдёт.`,
        tone: "warning",
      };
    default:
      return null;
  }
}
