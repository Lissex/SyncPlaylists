// В протоколе расширение → сервер нет cookie, токенов и Authorization площадок
// (ARCHITECTURE.md, 11h). Проверяется тремя способами:
// 1) все типы исходящих сообщений — глубокий обход ключей: запрещённые имена разрешены
//    только по точному списку путей (наш токен устройства в hello и поле session);
// 2) «канарейка»: страница площадки возвращает cookie/токен/заголовки с секретным
//    значением — после полного прогона WS-клиент → роутер ни в одном отправленном кадре нет
//    ни запрещённых ключей, ни значения-канарейки;
// 3) строгие схемы: лишнее поле в исходящем сообщении не отправляется вовсе.

import { fakeBrowser } from "wxt/testing/fake-browser";
import { beforeEach, describe, expect, it } from "vitest";
import { z } from "zod";

import { Journal } from "../src/background/journal";
import { Outbox } from "../src/background/outbox";
import type { OperationDef, PageOutcome } from "../src/background/registry";
import { TaskRouter } from "../src/background/router";
import { WsClient } from "../src/background/ws-client";
import { encodeOutgoing, type OutgoingMessage } from "../src/protocol";
import { FakeSocket, flush } from "./helpers";

const FORBIDDEN = /cookie|authorization|token|oauth|password|session/i;
// Точные пути (тип сообщения + путь ключа), где такие имена законны.
const ALLOWED_PATHS = new Set(["hello.token", "platform_state.session"]);
const CANARY = "canary-7f3a9c-secret";
const DEVICE_TOKEN = "device-token-0123456789";

function forbiddenPaths(message: Record<string, unknown>): string[] {
  const found: string[] = [];
  const walk = (value: unknown, path: string) => {
    if (Array.isArray(value)) {
      value.forEach((item, index) => walk(item, `${path}[${index}]`));
    } else if (value && typeof value === "object") {
      for (const [key, inner] of Object.entries(value)) {
        const next = `${path}.${key}`;
        if (FORBIDDEN.test(key) && !ALLOWED_PATHS.has(next)) found.push(next);
        walk(inner, next);
      }
    }
  };
  walk(message, String(message.type));
  return found;
}

// Каждый тип исходящего сообщения с максимально заполненными полями.
const EVERY_OUTGOING: OutgoingMessage[] = [
  { type: "hello", token: DEVICE_TOKEN, version: "0.1.0" },
  { type: "ping" },
  { type: "platform_state", platform: "soundcloud", session: "ok", external_user_id: "u1" },
  {
    type: "connect_platform",
    request_id: "r1",
    platform: "yandex",
    external_user_id: "u1",
    display_name: "Имя",
  },
  { type: "result", task_id: "t1", ok: true, data: { playlist_id: "p1", tracks: [{ id: "1" }] } },
  {
    type: "result",
    task_id: "t2",
    ok: false,
    error: { code: "rate_limited", message: "", retry_after: 3 },
  },
  { type: "progress", task_id: "t1" },
];

describe("исходящие сообщения", () => {
  it.each(EVERY_OUTGOING.map((m) => [m.type, m] as const))(
    "%s — без запрещённых ключей вне allowlist",
    (_, message) => {
      const encoded = JSON.parse(encodeOutgoing(message)) as Record<string, unknown>;
      expect(forbiddenPaths(encoded)).toEqual([]);
    },
  );

  it("allowlist — точные пути, а не «где угодно в hello»", () => {
    expect(forbiddenPaths({ type: "hello", token: "x", nested: { token: "y" } })).toEqual([
      "hello.nested.token",
    ]);
    expect(forbiddenPaths({ type: "result", data: { session: "x" } })).toEqual([
      "result.data.session",
    ]);
  });

  it("лишнее поле в исходящем сообщении — ошибка, а не отправка", () => {
    const withCookie = { type: "ping", cookie: CANARY } as unknown as OutgoingMessage;
    expect(() => encodeOutgoing(withCookie)).toThrow();
    const helloWithAuth = {
      type: "hello",
      token: DEVICE_TOKEN,
      version: "1",
      authorization: `OAuth ${CANARY}`,
    } as unknown as OutgoingMessage;
    expect(() => encodeOutgoing(helloWithAuth)).toThrow();
  });
});

describe("канарейка: страница отдаёт секреты, на сервер они не попадают", () => {
  beforeEach(() => fakeBrowser.reset());

  // Ответы «страницы» с секретами в разных местах.
  const leaks: Array<[string, unknown]> = [
    ["cookie рядом с данными", { ok: true, data: { playlist_id: "p1", cookie: CANARY } }],
    [
      "токен во вложенном объекте",
      { ok: true, data: { playlist_id: "p1", me: { oauth_token: CANARY } } },
    ],
    [
      "заголовки запроса",
      { ok: true, data: { playlist_id: "p1", headers: { Authorization: CANARY } } },
    ],
    ["секрет вместо данных", { ok: true, data: CANARY }],
    ["лишнее поле в ответе страницы", { ok: true, data: { playlist_id: "p1" }, cookie: CANARY }],
    ["секрет в коде ошибки", { ok: false, code: CANARY }],
  ];

  it.each(leaks)("%s", async (_, pageResult) => {
    const socket = new FakeSocket("ws://test");
    const def: OperationDef = {
      op: "soundcloud.create_playlist",
      target: {
        key: "soundcloud",
        origins: ["https://soundcloud.com/*"],
        tabUrl: "https://soundcloud.com/",
      },
      write: true,
      args: z.strictObject({ title: z.string() }),
      result: z.strictObject({ playlist_id: z.string() }),
      main: () => pageResult as PageOutcome,
    };
    const ws: WsClient = new WsClient({
      url: "ws://test",
      version: "0.1.0",
      makeSocket: () => socket,
      getToken: async () => DEVICE_TOKEN,
      onRevoked: async () => undefined,
      onState: () => undefined,
      onWelcome: () => undefined,
      onTask: (task) => void router.handle(task),
    });
    const router = new TaskRouter({
      registry: new Map([[def.op, def]]),
      journal: new Journal(),
      outbox: new Outbox(),
      hasPermission: async () => true,
      runInTarget: async (_target, main, args) => (main as (a: unknown) => unknown)(args),
      sendResult: (message) => ws.send(message),
      sendProgress: (taskId) => void ws.send({ type: "progress", task_id: taskId }),
    });

    await ws.ensure();
    socket.open();
    socket.receive({ type: "welcome", device_id: "d1", heartbeat_seconds: 20 });
    socket.receive({
      type: "task",
      task_id: "t1",
      op: "soundcloud.create_playlist",
      args: { title: "Мой плейлист" },
      deadline: Date.now() / 1000 + 60,
      idempotency_key: "create_playlist:r1",
    });
    await flush();
    await flush();

    const frames = socket.messages();
    expect(frames.map((f) => f.type)).toEqual(["hello", "result"]);
    for (const frame of frames) expect(forbiddenPaths(frame)).toEqual([]);
    expect(socket.sent.join("\n")).not.toContain(CANARY);
    expect(frames[1]).toMatchObject({ ok: false, error: { code: "bad_result" } });
    // И в журнал write-операций ничего не записано.
    expect(await new Journal().get("create_playlist:r1")).toBeNull();
    ws.stop();
  });
});
