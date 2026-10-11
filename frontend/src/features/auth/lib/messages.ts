import { ApiError } from "@/shared/api";

export const MIN_PASSWORD_LENGTH = 8;

function minutes(seconds: number): string {
  const value = Math.max(1, Math.ceil(seconds / 60));
  const mod10 = value % 10;
  const mod100 = value % 100;
  const word =
    mod10 === 1 && mod100 !== 11
      ? "минуту"
      : mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)
        ? "минуты"
        : "минут";
  return `${value} ${word}`;
}

/** Ошибка входа/регистрации — понятным текстом. */
export function authErrorMessage(error: unknown, mode: "login" | "register"): string {
  if (!(error instanceof ApiError)) return "Что-то пошло не так. Попробуйте ещё раз.";
  if (error.status === 429) {
    return error.retryAfter
      ? `Слишком много попыток. Попробуйте через ${minutes(error.retryAfter)}.`
      : "Слишком много попыток. Попробуйте позже.";
  }
  if (mode === "login" && error.status === 401) return "Неверный email или пароль";
  if (mode === "register" && error.status === 409) {
    return "Этот email уже зарегистрирован. Войдите или используйте другой.";
  }
  if (error.status === 422) {
    return error.code === "validation"
      ? `Проверьте email и пароль (не короче ${MIN_PASSWORD_LENGTH} символов)`
      : error.message;
  }
  return error.message;
}
