// Роутер задач сервера: задача → операция из фиксированного реестра → проверка
// аргументов → разрешение на сайт → журнал → темп записи → фоновая вкладка → функция в
// MAIN world (со сверкой аккаунта площадки) → проверка ответа схемой здесь, в background
// (изолированный контекст) → результат.

import type { TargetInfo } from "../platforms";
import {
  MAX_MESSAGE_CHARS,
  type ResultMessage,
  type SessionState,
  type TaskErrorCode,
  type TaskMessage,
} from "../protocol";
import type { Journal } from "./journal";
import type { Outbox } from "./outbox";
import { type AccountCheck, type OperationDef, type PageCall, pageOutcome } from "./registry";

export interface RouterDeps {
  registry: ReadonlyMap<string, OperationDef>;
  journal: Journal;
  outbox: Outbox;
  hasPermission(origins: string[]): Promise<boolean>;
  // Выполнить функцию операции в странице цели (вкладка + executeScript, world MAIN).
  // existingOnly — только в уже открытой вкладке цели, новую не открывать (нет — ошибка).
  runInTarget(
    target: TargetInfo,
    main: OperationDef["main"],
    args: unknown,
    existingOnly?: boolean,
  ): Promise<unknown>;
  // false — соединения нет (результат уйдёт в outbox).
  sendResult(message: ResultMessage): boolean;
  sendProgress(taskId: string): void;
  // Площадка сама сообщила о своём состоянии по ходу задачи (вышли из аккаунта, капча,
  // в браузере другой аккаунт) — сервер узнаёт сразу, presence обновляется.
  reportState?(key: string, session: SessionState, account: string | null): void;
  now?: () => number;
  sleep?: (ms: number) => Promise<void>;
  progressIntervalMs?: number;
  // Темп записи на площадку: не чаще одной write-операции за столько мс (ARCHITECTURE.md 11h).
  minWriteIntervalMs?: number;
  // Сколько полный запрос профиля (whoami) считается свежим для задач чтения.
  accountTtlMs?: number;
  // Сколько ждать ответа страницы при проверке площадки (вкладка + запрос профиля).
  probeTimeoutMs?: number;
}

// detail — понятная человеку причина сбоя проверки (для окна расширения, не для сервера).
export type ProbeResult =
  { ok: true; data: Record<string, unknown> } | { ok: false; code: TaskErrorCode; detail?: string };

function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`timeout ${Math.round(ms / 1000)} с`)), ms);
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (error: unknown) => {
        clearTimeout(timer);
        reject(error instanceof Error ? error : new Error(String(error)));
      },
    );
  });
}

function errorText(error: unknown): string {
  return (error instanceof Error ? error.message : String(error)).slice(0, 200);
}

class TaskFailure extends Error {
  constructor(
    readonly code: TaskErrorCode,
    readonly retryAfter?: number,
  ) {
    super(code);
  }
}

// Коды, по которым площадка недоступна до действия пользователя.
const SESSION_CODES: Partial<Record<TaskErrorCode, SessionState>> = {
  logged_out: "logged_out",
  captcha: "captcha",
};

export class TaskRouter {
  // Задачи одной цели (площадки) — по очереди: одна вкладка, темп записи; разные — параллельно.
  private readonly chains = new Map<string, Promise<void>>();
  private readonly lastWrite = new Map<string, number>();
  // Аккаунт площадки, проверенный запросом профиля: цель → {id, когда}.
  private readonly verified = new Map<string, { id: string; at: number }>();
  private readonly now: () => number;
  private readonly sleep: (ms: number) => Promise<void>;
  private readonly progressIntervalMs: number;
  private readonly minWriteIntervalMs: number;
  private readonly accountTtlMs: number;
  private readonly probeTimeoutMs: number;

  constructor(private readonly deps: RouterDeps) {
    this.now = deps.now ?? Date.now;
    this.sleep = deps.sleep ?? ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));
    this.progressIntervalMs = deps.progressIntervalMs ?? 10_000;
    this.minWriteIntervalMs = deps.minWriteIntervalMs ?? 1_000;
    this.accountTtlMs = deps.accountTtlMs ?? 60_000;
    this.probeTimeoutMs = deps.probeTimeoutMs ?? 45_000;
  }

  handle(task: TaskMessage): Promise<void> {
    const def = this.deps.registry.get(task.op);
    if (!def) return this.fail(task, new TaskFailure("unsupported_op"));
    const key = def.target.key;
    const previous = this.chains.get(key) ?? Promise.resolve();
    // Сбой одной задачи не должен останавливать очередь цели.
    const next = previous.catch(() => undefined).then(() => this.run(def, task));
    this.chains.set(key, next);
    return next;
  }

  // Проверка площадки по инициативе расширения (не сервера): кто вошёл на сайте. Идёт в
  // ту же очередь цели, что и задачи, — одна вкладка не делится между ними. null — ответа
  // нет (нет вкладки при existingOnly, страница не загрузилась).
  probe(op: string, existingOnly = false): Promise<ProbeResult | null> {
    const def = this.deps.registry.get(op);
    if (!def?.session) return Promise.resolve(null);
    const key = def.target.key;
    const previous = this.chains.get(key) ?? Promise.resolve();
    const next = previous.catch(() => undefined).then(() => this.runProbe(def, existingOnly));
    this.chains.set(
      key,
      next.then(() => undefined),
    );
    return next;
  }

  private async runProbe(def: OperationDef, existingOnly: boolean): Promise<ProbeResult | null> {
    if (!(await this.deps.hasPermission(def.target.origins))) {
      return { ok: false, code: "no_permission" };
    }
    const call: PageCall = {
      op: def.op.slice(def.op.indexOf(".") + 1),
      args: {},
      account: null,
      verify: "whoami",
    };
    let raw: unknown;
    try {
      raw = await withTimeout(
        this.deps.runInTarget(def.target, def.main, call, existingOnly),
        this.probeTimeoutMs,
      );
    } catch (error) {
      if (existingOnly) return null; // открытой вкладки площадки нет — это не сбой
      // Причина — только в консоль фона (на сервер не уходит): для диагностики.
      console.warn(`probe ${def.op}: страница не ответила`, error);
      return { ok: false, code: "unavailable", detail: errorText(error) };
    }
    const outcome = pageOutcome.safeParse(raw);
    if (!outcome.success) {
      console.warn(`probe ${def.op}: ответ страницы не по схеме`, outcome.error.issues);
      return { ok: false, code: "bad_result", detail: "ответ страницы не по схеме" };
    }
    if (!outcome.data.ok) {
      this.verified.delete(def.target.key);
      const failure: ProbeResult = { ok: false, code: outcome.data.code };
      if (outcome.data.detail) failure.detail = outcome.data.detail;
      return failure;
    }
    const data = def.result.safeParse(outcome.data.data);
    if (!data.success) {
      console.warn(`probe ${def.op}: данные не по схеме`, data.error.issues);
      return { ok: false, code: "bad_result", detail: "данные профиля не по схеме" };
    }
    if (outcome.data.account) {
      this.verified.set(def.target.key, { id: outcome.data.account, at: this.now() });
    }
    return { ok: true, data: data.data };
  }

  private async run(def: OperationDef, task: TaskMessage): Promise<void> {
    if (task.deadline * 1000 < this.now()) return; // ответ уже никто не ждёт
    const args = def.args.safeParse(task.args);
    if (!args.success) return this.fail(task, new TaskFailure("bad_args"));

    const key = def.write ? task.idempotency_key : null;
    if (key) {
      const done = await this.deps.journal.get(key);
      if (done) return this.succeed(task, done); // уже выполнено — в страницу не идём
    }
    if (!(await this.deps.hasPermission(def.target.origins))) {
      return this.fail(task, new TaskFailure("no_permission"));
    }

    const timer = setInterval(() => this.deps.sendProgress(task.task_id), this.progressIntervalMs);
    try {
      if (def.write) await this.pace(def.target.key);
      const data = await this.execute(def, args.data, task);
      if (key) await this.deps.journal.put(key, def.op, data); // до отправки результата
      await this.succeed(task, data);
    } catch (error) {
      await this.fail(task, error instanceof TaskFailure ? error : new TaskFailure("unavailable"));
    } finally {
      clearInterval(timer);
      if (def.write) this.lastWrite.set(def.target.key, this.now());
    }
  }

  private async pace(target: string): Promise<void> {
    const last = this.lastWrite.get(target);
    if (last === undefined) return;
    const wait = last + this.minWriteIntervalMs - this.now();
    if (wait > 0) await this.sleep(wait);
  }

  // Запись — по cookie (без запроса); чтение — полным профилем, если он не проверялся
  // последние accountTtlMs или проверялся другой аккаунт.
  private accountCheck(def: OperationDef, task: TaskMessage): AccountCheck {
    if (!task.account) return "none";
    if (def.write) return "cookie";
    const known = this.verified.get(def.target.key);
    const fresh = known && known.id === task.account && this.now() - known.at < this.accountTtlMs;
    return fresh ? "cookie" : "whoami";
  }

  private async execute(
    def: OperationDef,
    args: Record<string, unknown>,
    task: TaskMessage,
  ): Promise<Record<string, unknown>> {
    const input: unknown = def.session
      ? ({
          op: def.op.slice(def.op.indexOf(".") + 1),
          args,
          account: task.account ?? null,
          verify: this.accountCheck(def, task),
        } satisfies PageCall)
      : args;
    let raw: unknown;
    try {
      // Дольше дедлайна задачи ждать незачем: ответ сервер уже не примет.
      raw = await withTimeout(
        this.deps.runInTarget(def.target, def.main, input),
        Math.min(Math.max(task.deadline * 1000 - this.now(), 1_000), 600_000),
      );
    } catch {
      // Текст исключения (вкладку закрыли, страница не загрузилась) наружу не отдаём.
      throw new TaskFailure("unavailable");
    }
    const outcome = pageOutcome.safeParse(raw);
    if (!outcome.success) throw new TaskFailure("bad_result");
    const page = outcome.data;
    const target = def.target.key;
    if (!page.ok) {
      if (def.session) this.onSessionProblem(target, page.code, page.account ?? null);
      throw new TaskFailure(page.code, page.retry_after);
    }
    if (def.session && page.account)
      this.verified.set(target, { id: page.account, at: this.now() });
    const data = def.result.safeParse(page.data);
    if (!data.success) throw new TaskFailure("bad_result");
    if (JSON.stringify(data.data).length > MAX_MESSAGE_CHARS) throw new TaskFailure("bad_result");
    return data.data;
  }

  private onSessionProblem(target: string, code: TaskErrorCode, account: string | null): void {
    if (code === "session_mismatch") {
      // В браузере вошли в другой аккаунт: он готов к работе, но не для этой задачи.
      this.verified.delete(target);
      if (account) this.deps.reportState?.(target, "ok", account);
      return;
    }
    const state = SESSION_CODES[code];
    if (state) {
      this.verified.delete(target);
      this.deps.reportState?.(target, state, null);
    }
  }

  private succeed(task: TaskMessage, data: Record<string, unknown>): Promise<void> {
    return this.send({ type: "result", task_id: task.task_id, ok: true, data });
  }

  private fail(task: TaskMessage, failure: TaskFailure): Promise<void> {
    const error: { code: TaskErrorCode; message: string; retry_after?: number } = {
      code: failure.code,
      message: "",
    };
    if (failure.retryAfter !== undefined) error.retry_after = failure.retryAfter;
    return this.send({ type: "result", task_id: task.task_id, ok: false, error });
  }

  private async send(message: ResultMessage): Promise<void> {
    if (!this.deps.sendResult(message)) await this.deps.outbox.put(message);
  }
}
