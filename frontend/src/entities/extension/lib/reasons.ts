/**
 * Почему расширение сейчас не может выполнить операцию (pause_reason переноса и
 * `reason` ответа 409 extension_unavailable) — что сделать пользователю.
 */
export function extensionReasonText(reason: string | null | undefined, site: string): string {
  switch (reason) {
    case "offline":
      return "Браузер с расширением закрыт. Откройте его — продолжим сами.";
    case "timeout":
      return "Браузер не ответил вовремя. Проверьте, что он открыт и не спит, — повторим сами.";
    case "logged_out":
      return `Войдите на ${site} в браузере с расширением — продолжим сами.`;
    case "captcha":
      return `Откройте ${site} в браузере и пройдите проверку — продолжим сами.`;
    case "no_permission":
      return `Разрешите расширению доступ к ${site}: откройте расширение и включите площадку.`;
    case "session_mismatch":
      return `В браузере открыт другой аккаунт на ${site}. Войдите в тот, что подключён к сервису.`;
    default:
      return "Расширение в браузере сейчас недоступно. Откройте браузер с расширением.";
  }
}
