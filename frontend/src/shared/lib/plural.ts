/** Русское склонение по числу: plural(5, ["трек", "трека", "треков"]) → «треков». */
export function plural(count: number, forms: readonly [string, string, string]): string {
  const mod10 = Math.abs(count) % 10;
  const mod100 = Math.abs(count) % 100;
  if (mod10 === 1 && mod100 !== 11) return forms[0];
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return forms[1];
  return forms[2];
}

/** «248 треков». */
export function countOf(count: number, forms: readonly [string, string, string]): string {
  return `${count.toLocaleString("ru")} ${plural(count, forms)}`;
}

export const TRACKS = ["трек", "трека", "треков"] as const;
