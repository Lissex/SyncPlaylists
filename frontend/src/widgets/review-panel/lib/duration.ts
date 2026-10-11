/** Разница длительностей кандидата и трека источника: «+0:03», «−1:10»; null — не с чем сравнить. */
export function durationDelta(
  candidateMs: number | null | undefined,
  sourceMs: number | null | undefined,
): { text: string; large: boolean } | null {
  if (candidateMs == null || sourceMs == null) return null;
  const diff = Math.round((candidateMs - sourceMs) / 1000);
  const abs = Math.abs(diff);
  const text = `${diff < 0 ? "−" : "+"}${Math.floor(abs / 60)}:${String(abs % 60).padStart(2, "0")}`;
  // Больше 10 секунд — скорее другая версия (live, remix, radio edit).
  return { text, large: abs > 10 };
}
