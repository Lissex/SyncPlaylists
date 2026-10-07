import { fakeBrowser } from "wxt/testing/fake-browser";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { z } from "zod";

import { Journal } from "../src/background/journal";
import { Outbox } from "../src/background/outbox";
import { buildRegistry, type OperationDef, type PageOutcome } from "../src/background/registry";
import { type RouterDeps, TaskRouter } from "../src/background/router";
import type { ResultMessage, TaskMessage } from "../src/protocol";

const WRITE: OperationDef = {
  op: "soundcloud.create_playlist",
  target: {
    key: "soundcloud",
    origins: ["https://soundcloud.com/*"],
    tabUrl: "https://soundcloud.com/",
  },
  write: true,
  args: z.strictObject({ title: z.string() }),
  result: z.strictObject({ playlist_id: z.string() }),
  main: () => ({ ok: true, data: { playlist_id: "p1" } }),
};

function task(overrides: Partial<TaskMessage> = {}): TaskMessage {
  return {
    type: "task",
    task_id: "t1",
    op: WRITE.op,
    args: { title: "Мой" },
    deadline: Date.now() / 1000 + 60,
    idempotency_key: "create_playlist:r1",
    ...overrides,
  };
}

function setup(overrides: Partial<RouterDeps> = {}) {
  const sent: ResultMessage[] = [];
  const page = vi.fn(async (): Promise<unknown> => ({ ok: true, data: { playlist_id: "p1" } }));
  const deps: RouterDeps = {
    registry: new Map([[WRITE.op, WRITE]]),
    journal: new Journal(),
    outbox: new Outbox(),
    hasPermission: async () => true,
    runInTarget: page,
    sendResult: (message) => {
      sent.push(message);
      return true;
    },
    sendProgress: () => undefined,
    ...overrides,
  };
  return { router: new TaskRouter(deps), sent, page, deps };
}

beforeEach(() => fakeBrowser.reset());

describe("роутер задач", () => {
  it("выполняет операцию и отправляет проверенный результат", async () => {
    const { router, sent, page } = setup();

    await router.handle(task());

    expect(page).toHaveBeenCalledWith(WRITE.target, WRITE.main, { title: "Мой" });
    expect(sent).toEqual([
      { type: "result", task_id: "t1", ok: true, data: { playlist_id: "p1" } },
    ]);
  });

  it("неизвестная операция — unsupported_op, в страницу не идёт", async () => {
    const { router, sent, page } = setup();

    await router.handle(task({ op: "soundcloud.delete_everything" }));

    expect(page).not.toHaveBeenCalled();
    expect(sent[0]).toMatchObject({ ok: false, error: { code: "unsupported_op" } });
  });

  it("аргументы не по схеме — bad_args, в страницу не идёт", async () => {
    const { router, sent, page } = setup();

    await router.handle(task({ args: { title: "x", url: "https://evil.example" } }));

    expect(page).not.toHaveBeenCalled();
    expect(sent[0]).toMatchObject({ ok: false, error: { code: "bad_args" } });
  });

  it("нет разрешения на сайт — no_permission", async () => {
    const { router, sent, page } = setup({ hasPermission: async () => false });

    await router.handle(task());

    expect(page).not.toHaveBeenCalled();
    expect(sent[0]).toMatchObject({ ok: false, error: { code: "no_permission" } });
  });

  it("истёкшая задача не выполняется и не отвечается", async () => {
    const { router, sent, page } = setup();

    await router.handle(task({ deadline: Date.now() / 1000 - 1 }));

    expect(page).not.toHaveBeenCalled();
    expect(sent).toEqual([]);
  });

  it("ошибка страницы — код из закрытого списка, текст не уходит", async () => {
    const { router, sent } = setup({
      runInTarget: async () => ({ ok: false, code: "captcha" }) satisfies PageOutcome,
    });

    await router.handle(task());

    expect(sent[0]).toEqual({
      type: "result",
      task_id: "t1",
      ok: false,
      error: { code: "captcha", message: "" },
    });
  });

  it("исключение при исполнении — unavailable без текста исключения", async () => {
    const { router, sent } = setup({
      runInTarget: async () => {
        throw new Error("No tab with id 42: user@example.com");
      },
    });

    await router.handle(task());

    expect(sent[0]).toEqual({
      type: "result",
      task_id: "t1",
      ok: false,
      error: { code: "unavailable", message: "" },
    });
  });

  it("повтор write-задачи с тем же ключом — из журнала, без второго создания", async () => {
    const { router, sent, page } = setup();

    await router.handle(task());
    await router.handle(task({ task_id: "t2" })); // сервер повторил: результат потерялся

    expect(page).toHaveBeenCalledTimes(1);
    expect(sent.map((m) => [m.task_id, m.data])).toEqual([
      ["t1", { playlist_id: "p1" }],
      ["t2", { playlist_id: "p1" }],
    ]);
  });

  it("другой ключ — новая запись", async () => {
    const { router, page } = setup();

    await router.handle(task());
    await router.handle(task({ task_id: "t2", idempotency_key: "create_playlist:r2" }));

    expect(page).toHaveBeenCalledTimes(2);
  });

  it("нет связи — результат в outbox и уходит после переподключения", async () => {
    let online = false;
    const sent: ResultMessage[] = [];
    const { router, deps } = setup({
      sendResult: (message) => {
        if (!online) return false;
        sent.push(message);
        return true;
      },
    });

    await router.handle(task());
    expect(sent).toEqual([]);

    online = true;
    await deps.outbox.flush((message) => deps.sendResult(message));
    expect(sent).toEqual([
      { type: "result", task_id: "t1", ok: true, data: { playlist_id: "p1" } },
    ]);
    // Повторно не досылается.
    await deps.outbox.flush((message) => deps.sendResult(message));
    expect(sent).toHaveLength(1);
  });

  it("задачи одной площадки — по очереди, сбой одной не останавливает следующую", async () => {
    const order: string[] = [];
    let calls = 0;
    const { router, sent } = setup({
      runInTarget: async () => {
        calls += 1;
        const mine = calls;
        order.push(`start ${mine}`);
        await new Promise((resolve) => setTimeout(resolve, 5));
        order.push(`end ${mine}`);
        if (mine === 1) throw new Error("boom");
        return { ok: true, data: { playlist_id: `p${mine}` } };
      },
    });

    await Promise.all([
      router.handle(task({ task_id: "a", idempotency_key: null })),
      router.handle(task({ task_id: "b", idempotency_key: null })),
    ]);

    expect(order).toEqual(["start 1", "end 1", "start 2", "end 2"]);
    expect(sent.map((m) => m.ok)).toEqual([false, true]);
  });

  it("долгая операция шлёт progress", async () => {
    const progress: string[] = [];
    const { router } = setup({
      progressIntervalMs: 5,
      sendProgress: (taskId) => progress.push(taskId),
      runInTarget: async () => {
        await new Promise((resolve) => setTimeout(resolve, 20));
        return { ok: true, data: { playlist_id: "p1" } };
      },
    });

    await router.handle(task());

    expect(progress.length).toBeGreaterThan(0);
    expect(new Set(progress)).toEqual(new Set(["t1"]));
  });
});

describe("diagnostics.echo", () => {
  it("echo возвращает nonce и страницу; ответ проходит схему", async () => {
    const registry = buildRegistry({ apiBase: "http://localhost:8000", diagnostics: true });
    const def = registry.get("diagnostics.echo")!;
    // Функция страницы читает document/location — подставляем «страницу».
    vi.stubGlobal("document", { title: "SyncPlaylists — проверка связи" });
    vi.stubGlobal("location", { pathname: "/extension/diagnostics/page" });
    const { router, sent } = setup({
      registry,
      runInTarget: async (_target, main, args) => (main as (a: unknown) => unknown)(args),
    });

    await router.handle(task({ op: def.op, args: { nonce: "n1" }, idempotency_key: null }));

    expect(def.target.tabUrl).toBe("http://localhost:8000/extension/diagnostics/page");
    expect(sent[0]).toMatchObject({
      ok: true,
      data: {
        nonce: "n1",
        page_title: "SyncPlaylists — проверка связи",
        path: "/extension/diagnostics/page",
      },
    });
  });
});
