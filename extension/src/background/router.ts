// Роутер задач сервера: задача → операция из фиксированного реестра → проверка
// аргументов → разрешение на сайт → журнал → фоновая вкладка → функция в MAIN world →
// проверка ответа схемой здесь, в background (изолированный контекст) → результат.

import type { TargetInfo } from "../platforms";
import {
  MAX_MESSAGE_CHARS,
  type ResultMessage,
  type TaskErrorCode,
  type TaskMessage,
} from "../protocol";
import type { Journal } from "./journal";
import type { Outbox } from "./outbox";
import { type OperationDef, pageOutcome } from "./registry";

export interface RouterDeps {
  registry: ReadonlyMap<string, OperationDef>;
  journal: Journal;
  outbox: Outbox;
  hasPermission(origins: string[]): Promise<boolean>;
  // Выполнить функцию операции в странице цели (вкладка + executeScript, world MAIN).
  runInTarget(target: TargetInfo, main: OperationDef["main"], args: unknown): Promise<unknown>;
  // false — соединения нет (результат уйдёт в outbox).
  sendResult(message: ResultMessage): boolean;
  sendProgress(taskId: string): void;
  now?: () => number;
  progressIntervalMs?: number;
}

class TaskFailure extends Error {
  constructor(readonly code: TaskErrorCode) {
    super(code);
  }
}

export class TaskRouter {
  // Задачи одной цели (площадки) — по очереди: одна вкладка, темп записи; разные — параллельно.
  private readonly chains = new Map<string, Promise<void>>();
  private readonly now: () => number;
  private readonly progressIntervalMs: number;

  constructor(private readonly deps: RouterDeps) {
    this.now = deps.now ?? Date.now;
    this.progressIntervalMs = deps.progressIntervalMs ?? 10_000;
  }

  handle(task: TaskMessage): Promise<void> {
    const def = this.deps.registry.get(task.op);
    if (!def) return this.fail(task, "unsupported_op");
    const key = def.target.key;
    const previous = this.chains.get(key) ?? Promise.resolve();
    // Сбой одной задачи не должен останавливать очередь цели.
    const next = previous.catch(() => undefined).then(() => this.run(def, task));
    this.chains.set(key, next);
    return next;
  }

  private async run(def: OperationDef, task: TaskMessage): Promise<void> {
    if (task.deadline * 1000 < this.now()) return; // ответ уже никто не ждёт
    const args = def.args.safeParse(task.args);
    if (!args.success) return this.fail(task, "bad_args");

    const key = def.write ? task.idempotency_key : null;
    if (key) {
      const done = await this.deps.journal.get(key);
      if (done) return this.succeed(task, done); // уже выполнено — в страницу не идём
    }
    if (!(await this.deps.hasPermission(def.target.origins))) {
      return this.fail(task, "no_permission");
    }

    const timer = setInterval(() => this.deps.sendProgress(task.task_id), this.progressIntervalMs);
    try {
      const data = await this.execute(def, args.data);
      if (key) await this.deps.journal.put(key, def.op, data); // до отправки результата
      await this.succeed(task, data);
    } catch (error) {
      await this.fail(task, error instanceof TaskFailure ? error.code : "unavailable");
    } finally {
      clearInterval(timer);
    }
  }

  private async execute(def: OperationDef, args: unknown): Promise<Record<string, unknown>> {
    let raw: unknown;
    try {
      raw = await this.deps.runInTarget(def.target, def.main, args);
    } catch {
      // Текст исключения (вкладку закрыли, страница не загрузилась) наружу не отдаём.
      throw new TaskFailure("unavailable");
    }
    const outcome = pageOutcome.safeParse(raw);
    if (!outcome.success) throw new TaskFailure("bad_result");
    if (!outcome.data.ok) throw new TaskFailure(outcome.data.code);
    const data = def.result.safeParse(outcome.data.data);
    if (!data.success) throw new TaskFailure("bad_result");
    if (JSON.stringify(data.data).length > MAX_MESSAGE_CHARS) throw new TaskFailure("bad_result");
    return data.data;
  }

  private succeed(task: TaskMessage, data: Record<string, unknown>): Promise<void> {
    return this.send({ type: "result", task_id: task.task_id, ok: true, data });
  }

  private fail(task: TaskMessage, code: TaskErrorCode): Promise<void> {
    return this.send({
      type: "result",
      task_id: task.task_id,
      ok: false,
      error: { code, message: "" },
    });
  }

  private async send(message: ResultMessage): Promise<void> {
    if (!this.deps.sendResult(message)) await this.deps.outbox.put(message);
  }
}
