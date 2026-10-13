import type { Account } from "@/entities/account";
import type { PlatformSession } from "@/entities/extension";
import type { PlatformInfo } from "@/entities/platform";

export type Tone = "ok" | "warning" | "danger" | "muted";

export type ConnectionState = {
  tone: Tone;
  /** Коротко — справа в строке. */
  label: string;
  /** Что сделать пользователю (или что это значит). */
  hint: string | null;
  /** Аккаунт, который можно отключить. */
  account: Account | null;
};

/**
 * Состояние площадки для экрана «Подключения»: аккаунт на сервере + сессия в браузере
 * (что сообщило расширение) + на связи ли расширение вообще.
 */
export function connectionState(
  platform: PlatformInfo,
  accounts: readonly Account[],
  session: PlatformSession | undefined,
  extensionOnline: boolean,
): ConnectionState {
  if (!platform.available) {
    return { tone: "muted", label: "Скоро", hint: null, account: null };
  }
  const account =
    accounts.find((a) => a.platform === platform.id && a.status !== "disconnected") ?? null;
  const state = session?.state ?? null;

  if (account?.status === "expired") {
    return {
      tone: "danger",
      label: "Нужно переподключить",
      hint: `Доступ к ${platform.name} истёк. Отключите и подключите площадку снова в расширении.`,
      account,
    };
  }

  if (!account) {
    if (state === "ok") {
      return {
        tone: "warning",
        label: "Не подключена",
        hint: `Вход на ${platform.site} есть — нажмите «Подключить» в расширении.`,
        account: null,
      };
    }
    return {
      tone: "muted",
      label: "Не подключена",
      hint: extensionOnline
        ? `Откройте расширение и включите ${platform.name}.`
        : "Подключается через браузерное расширение.",
      account: null,
    };
  }

  // Аккаунт с сервером напрямую (токен/OAuth) от браузера не зависит.
  if (account.transport !== "extension") {
    return { tone: "ok", label: "Подключена", hint: account.display_name, account };
  }

  switch (state) {
    case "ok":
      if (session?.external_user_id && session.external_user_id !== account.external_user_id) {
        return {
          tone: "warning",
          label: "Другой аккаунт",
          hint: `В браузере открыт другой аккаунт ${platform.name}. Войдите в «${account.display_name ?? account.external_user_id}» или переподключите площадку.`,
          account,
        };
      }
      return { tone: "ok", label: "Готова", hint: account.display_name, account };
    case "logged_out":
      return {
        tone: "warning",
        label: "Нужен вход",
        hint: `Войдите на ${platform.site} в браузере с расширением.`,
        account,
      };
    case "captcha":
      return {
        tone: "warning",
        label: "Проверка",
        hint: `${platform.name} просит пройти проверку — откройте ${platform.site} в браузере.`,
        account,
      };
    case "no_permission":
      return {
        tone: "warning",
        label: "Нет доступа",
        hint: `Разрешите расширению доступ к ${platform.site}: откройте расширение и включите площадку.`,
        account,
      };
    default:
      return {
        tone: "muted",
        label: "Браузер закрыт",
        hint: "Подключена. Переносы идут, пока открыт браузер с расширением.",
        account,
      };
  }
}
