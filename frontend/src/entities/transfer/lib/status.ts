export type TransferStatus =
  | "queued"
  | "running"
  | "paused_captcha"
  | "paused_quota"
  | "paused_client"
  | "review"
  | "writing"
  | "done"
  | "failed";

export const FINISHED: ReadonlySet<string> = new Set(["done", "failed"]);
export const PAUSED: ReadonlySet<string> = new Set([
  "paused_captcha",
  "paused_quota",
  "paused_client",
]);

export function isFinished(status: string): boolean {
  return FINISHED.has(status);
}

export function isPaused(status: string): boolean {
  return PAUSED.has(status);
}

const LABELS: Record<TransferStatus, string> = {
  queued: "В очереди",
  running: "Ищем треки",
  paused_captcha: "Пауза: проверка",
  paused_quota: "Пауза: лимит площадки",
  paused_client: "Ждём браузер",
  review: "Нужна проверка",
  writing: "Записываем",
  done: "Готово",
  failed: "Ошибка",
};

export function statusLabel(status: string): string {
  return LABELS[status as TransferStatus] ?? status;
}

export type Step = "read" | "match" | "review" | "write" | "done";

export const STEPS: readonly { id: Step; label: string }[] = [
  { id: "read", label: "Чтение" },
  { id: "match", label: "Поиск" },
  { id: "review", label: "Проверка" },
  { id: "write", label: "Запись" },
  { id: "done", label: "Готово" },
];

/**
 * Текущий шаг по статусу и счётчикам. Пауза остаётся на шаге, где случилась: до чтения
 * источника (total = 0) — чтение, пока есть необработанные — поиск, после — запись.
 */
export function currentStep(status: string, total: number, pending: number): Step {
  switch (status) {
    case "queued":
      return "read";
    case "review":
      return "review";
    case "writing":
      return "write";
    case "done":
      return "done";
    default:
      if (total === 0) return "read";
      if (pending > 0) return "match";
      return "write";
  }
}

const METHODS: Record<string, string> = {
  isrc: "по коду ISRC",
  fuzzy: "по названию",
  cache: "из прошлых переносов",
  same_platform: "та же площадка",
  audio: "по звуку",
  manual: "выбрано вами",
};

export function methodLabel(method: string): string {
  return METHODS[method] ?? method;
}

/** «≈ 4 мин», «≈ 1 ч 20 мин», «меньше минуты». */
export function formatEta(seconds: number): string {
  if (seconds < 60) return "меньше минуты";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `≈ ${minutes} мин`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `≈ ${hours} ч ${rest} мин` : `≈ ${hours} ч`;
}

/** «4:45» из миллисекунд. */
export function formatDuration(ms: number | null | undefined): string | null {
  if (ms == null) return null;
  const total = Math.round(ms / 1000);
  const minutes = Math.floor(total / 60);
  const seconds = String(total % 60).padStart(2, "0");
  return `${minutes}:${seconds}`;
}
