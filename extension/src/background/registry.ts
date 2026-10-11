// Фиксированный реестр операций: расширение выполняет только то, что здесь описано, —
// имя операции и данные приходят с сервера, код никогда. Сервер держит такой же реестр
// (modules/extension/application/operations.py).
//
// У каждой операции:
// - args — строгая схема аргументов: в страницу уходят только они;
// - main — функция, которая выполняется в MAIN world страницы площадки
//   (scripting.executeScript сериализует её через toString(): она должна быть
//   самодостаточной — без замыканий, импортов и внешних функций);
// - result — строгая схема ответа: проверяется в background до отправки на сервер,
//   лишнее поле (cookie, токен из страницы) — ошибка bad_result, ничего не уходит;
// - write — меняет данные на площадке: результат пишется в журнал по ключу
//   идемпотентности, повтор задачи с тем же ключом в страницу не уходит;
// - session — операция площадки со сверкой аккаунта: main получает конверт PageCall
//   (имя операции, аргументы, ожидаемый аккаунт и способ сверки), а не голые аргументы.

import { z } from "zod";

import { diagnosticsTarget, type TargetInfo } from "../platforms";
import { soundcloudOperations } from "./soundcloud";
import { vkOperations } from "./vk";
import { yandexOperations } from "./yandex";

// Что возвращает функция из страницы. Код ошибки — из закрытого списка: текст ошибки
// страницы (там могут быть персональные данные) не передаётся.
export const PAGE_ERROR_CODES = [
  "logged_out",
  "session_mismatch",
  "captcha",
  "not_found",
  "not_writable",
  "rate_limited",
  "unavailable",
  // Изменилось с момента чтения (ревизия плейлиста или медиатеки Яндекса) — перечитать.
  "conflict",
  // Площадка не приняла запрос (400) — повтор не поможет.
  "bad_request",
  // Веб-токен страницы истёк (VK) — только внутри расширения: роутер перезагружает вкладку
  // и повторяет задачу; на сервер не уходит.
  "token_expired",
] as const;
export type PageErrorCode = (typeof PAGE_ERROR_CODES)[number];
// account — id аккаунта площадки, который страница проверила полным запросом профиля
// (или нашла вместо ожидаемого при session_mismatch); hint — что сверять в следующий раз
// без запроса (логин Яндекса из cookie yandex_login); retry_after — для rate_limited.
export type PageOutcome =
  | { ok: true; data: unknown; account?: string; hint?: string }
  | { ok: false; code: PageErrorCode; account?: string; retry_after?: number; detail?: string };

const accountId = z.string().min(1).max(200);
export const pageOutcome = z.union([
  z.strictObject({
    ok: z.literal(true),
    data: z.unknown(),
    account: accountId.optional(),
    hint: accountId.optional(),
  }),
  z.strictObject({
    ok: z.literal(false),
    code: z.enum(PAGE_ERROR_CODES),
    account: accountId.optional(),
    retry_after: z.number().positive().max(86400).optional(),
    // Техническая причина для окна расширения (на сервер не отправляется).
    detail: z.string().max(200).optional(),
  }),
]);

// Как сверять аккаунт в странице перед операцией: "whoami" — запросом профиля,
// "cookie" — по id из cookie сессии (без запроса), "none" — не сверять.
export type AccountCheck = "whoami" | "cookie" | "none";

// Конверт вызова операции площадки в странице (OperationDef.session).
export interface PageCall {
  op: string; // имя операции без площадки ("like")
  args: Record<string, unknown>;
  account: string | null; // аккаунт площадки задачи; null — не сверять
  verify: AccountCheck;
  // Подсказка для сверки без запроса: что страница вернула в hint при последней полной
  // проверке этого аккаунта (Яндекс — логин). Нет — сверка полным запросом.
  account_hint?: string | null;
}

export interface OperationDef {
  op: string; // "<площадка>.<операция>", как wire_op на сервере
  target: TargetInfo;
  write: boolean;
  session?: boolean;
  args: z.ZodType<Record<string, unknown>>;
  result: z.ZodType<Record<string, unknown>>;
  main: (args: never) => PageOutcome | Promise<PageOutcome>;
  // Темп самой операции: не чаще раза в minIntervalMs (+ случайно до jitterMs) — для
  // бережных записей (VK «Моя музыка»: 2–3 с), сверх общего темпа цели.
  minIntervalMs?: number;
  jitterMs?: number;
}

// ------------------------------------------------------------------ diagnostics.echo

const echoArgs = z.strictObject({ nonce: z.string().min(1).max(64) });
const echoResult = z.strictObject({
  nonce: z.string().max(64),
  page_title: z.string().max(200),
  path: z.string().max(200),
});

// Выполняется в странице: возвращает nonce и что это за страница.
function echoInPage(args: { nonce: string }): PageOutcome {
  return {
    ok: true,
    data: {
      nonce: args.nonce,
      page_title: document.title.slice(0, 200),
      path: location.pathname,
    },
  };
}

export interface RegistryOptions {
  apiBase: string;
  diagnostics: boolean;
}

export function buildRegistry(options: RegistryOptions): ReadonlyMap<string, OperationDef> {
  const defs: OperationDef[] = [];
  if (options.diagnostics) {
    defs.push({
      op: "diagnostics.echo",
      target: diagnosticsTarget(options.apiBase),
      write: false,
      args: echoArgs,
      result: echoResult,
      main: echoInPage,
    });
  }
  defs.push(...soundcloudOperations());
  defs.push(...yandexOperations());
  defs.push(...vkOperations());
  return new Map(defs.map((def) => [def.op, def]));
}
