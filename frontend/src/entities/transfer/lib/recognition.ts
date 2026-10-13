import type { TransferItem } from "../api/transfer";

export type ItemRecognition = NonNullable<TransferItem["recognition"]>;

/**
 * Причина итога распознавания по звуку — коротко для ревью и отчёта. null — показывать
 * нечего (найдено по звуку: это видно по статусу).
 */
const NOTES: Record<string, string> = {
  altered: "звук изменён (скорость или тон) — проверьте",
  text_mismatch: "не похоже на название — проверьте",
  not_on_target: "на площадке назначения точного совпадения нет",
  no_match: "по звуку не узнан",
  no_audio: "площадка не отдаёт звук этого трека",
  unavailable: "распознать не получилось",
  timeout: "не успели распознать",
};

export function recognitionNote(recognition: ItemRecognition | null | undefined): string | null {
  if (!recognition) return null;
  if (recognition.state !== "final") return "Распознаём по звуку…";
  return recognition.note ? (NOTES[recognition.note] ?? null) : null;
}

/** «По звуку: Juice WRLD — Lucid Dreams» — что услышал распознаватель. */
export function heardText(recognition: ItemRecognition | null | undefined): string | null {
  const heard = recognition?.heard;
  return heard ? `По звуку: ${heard.artist} — ${heard.title}` : null;
}
