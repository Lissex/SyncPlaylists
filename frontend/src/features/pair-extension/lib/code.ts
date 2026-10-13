// Код из расширения: 8 символов без похожих (0/O, 1/I/L), показывается как XXXX-XXXX.
const ALLOWED = /[^ABCDEFGHJKMNPQRSTUVWXYZ23456789]/g;
export const CODE_LENGTH = 8;

/** Как ввёл человек → «ABCD-EFGH»: регистр, пробелы и дефисы не важны. */
export function formatPairingCode(input: string): string {
  const raw = input.toUpperCase().replace(ALLOWED, "").slice(0, CODE_LENGTH);
  return raw.length > 4 ? `${raw.slice(0, 4)}-${raw.slice(4)}` : raw;
}

export function isCompleteCode(code: string): boolean {
  return code.replace("-", "").length === CODE_LENGTH;
}
