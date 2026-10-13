import { z } from "zod";

/**
 * Куда вернуть после входа. Только путь внутри сайта: «/…», но не «//host» и не
 * «/\host» — иначе ссылка вида /login?redirect=//evil.example увела бы на чужой сайт.
 */
export function safeRedirect(value: unknown): string | undefined {
  if (typeof value !== "string" || !value.startsWith("/")) return undefined;
  if (value.startsWith("//") || value.startsWith("/\\")) return undefined;
  return value;
}

export const authSearchSchema = z.object({
  redirect: z.unknown().transform(safeRedirect).optional(),
});
