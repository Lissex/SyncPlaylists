import type { Account } from "@/entities/account";
import { platformInfo } from "@/entities/platform";

import { connectionState } from "./connection-state";

const account = (overrides: Partial<Account> = {}): Account => ({
  id: "acc-1",
  platform: "yandex",
  transport: "extension",
  status: "active",
  external_user_id: "uid-1",
  display_name: "Я",
  expires_at: null,
  connected_at: "2026-10-01T00:00:00Z",
  ...overrides,
});

const yandex = platformInfo("yandex");
const session = (
  state: "ok" | "logged_out" | "captcha" | "no_permission" | null,
  uid = "uid-1",
) => ({
  platform: "yandex" as const,
  state,
  device_id: state ? "dev-1" : null,
  external_user_id: state ? uid : null,
});

describe("connectionState", () => {
  it("is ready when the browser session matches the account", () => {
    expect(connectionState(yandex, [account()], session("ok"), true)).toMatchObject({
      tone: "ok",
      label: "Готова",
    });
  });

  it("warns about another account opened in the browser", () => {
    const state = connectionState(yandex, [account()], session("ok", "uid-2"), true);
    expect(state).toMatchObject({ tone: "warning", label: "Другой аккаунт" });
  });

  it.each([
    ["logged_out", "Нужен вход", "Войдите на music.yandex.ru"],
    ["captcha", "Проверка", "пройти проверку"],
    ["no_permission", "Нет доступа", "Разрешите расширению доступ"],
  ] as const)("explains %s", (state, label, hint) => {
    const result = connectionState(yandex, [account()], session(state), true);
    expect(result.label).toBe(label);
    expect(result.hint).toContain(hint);
  });

  it("says the browser is closed when nothing reports the platform", () => {
    expect(connectionState(yandex, [account()], session(null), false).label).toBe("Браузер закрыт");
  });

  it("asks to reconnect an expired account", () => {
    const state = connectionState(yandex, [account({ status: "expired" })], session("ok"), true);
    expect(state).toMatchObject({ tone: "danger", label: "Нужно переподключить" });
  });

  it("ignores disconnected accounts and suggests connecting in the extension", () => {
    const state = connectionState(
      yandex,
      [account({ status: "disconnected" })],
      session("ok"),
      true,
    );
    expect(state).toMatchObject({ label: "Не подключена", account: null });
    expect(state.hint).toContain("«Подключить» в расширении");
  });

  it("marks platforms that are not available yet", () => {
    expect(connectionState(platformInfo("spotify"), [], undefined, true).label).toBe("Скоро");
  });

  it("server-side accounts do not depend on the browser", () => {
    const state = connectionState(yandex, [account({ transport: "unofficial" })], undefined, false);
    expect(state).toMatchObject({ tone: "ok", label: "Подключена" });
  });
});
