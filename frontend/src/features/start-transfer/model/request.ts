import type { Platform } from "@/entities/platform";
import type { StartTransferRequest } from "@/entities/transfer";

/** Откуда переносим: ссылка (сервер разберёт сам) или медиатека подключённого аккаунта. */
export type SourceChoice =
  { kind: "link"; url: string } | { kind: "library"; platform: Platform; accountId: string };

export type DestinationKind = "new" | "existing" | "library";

/** Куда переносим: всегда на площадку подключённого аккаунта. */
export type DestinationChoice = {
  platform: Platform;
  accountId: string;
  kind: DestinationKind;
  /** Новый плейлист: название. */
  title: string;
  /** Существующий плейлист: ссылка на него. */
  url: string;
};

export const MAX_TITLE_LENGTH = 100;

/** Тело POST /transfers. null — выбор неполный (кнопка «Перенести» неактивна). */
export function buildStartRequest(
  source: SourceChoice | null,
  destination: DestinationChoice | null,
  recognizeAudio = true,
): StartTransferRequest | null {
  if (!source || !destination) return null;
  const options = { recognize_audio: recognizeAudio };
  const sourcePart: StartTransferRequest["source"] =
    source.kind === "link"
      ? { kind: "link", url: source.url.trim() }
      : { kind: "library", platform: source.platform, account_id: source.accountId };

  switch (destination.kind) {
    case "new": {
      const title = destination.title.trim();
      if (!title || title.length > MAX_TITLE_LENGTH) return null;
      return {
        source: sourcePart,
        destination: { kind: "new", platform: destination.platform, title },
        ...options,
      };
    }
    case "existing": {
      const url = destination.url.trim();
      if (!url) return null;
      return { source: sourcePart, destination: { kind: "link", url }, ...options };
    }
    case "library":
      return {
        source: sourcePart,
        destination: {
          kind: "library",
          platform: destination.platform,
          account_id: destination.accountId,
        },
        ...options,
      };
  }
}
