import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  backoffDelay,
  CLOSE_BAD_ORIGIN,
  CLOSE_UNAUTHORIZED,
  type ConnectionState,
  WsClient,
  type WsClientDeps,
} from "../src/background/ws-client";
import type { TaskMessage } from "../src/protocol";
import { FakeSocket } from "./helpers";

const TOKEN = "device-token-0123456789";

function setup(overrides: Partial<WsClientDeps> = {}) {
  const sockets: FakeSocket[] = [];
  const states: ConnectionState[] = [];
  const tasks: TaskMessage[] = [];
  let token: string | null = TOKEN;
  const revoked = vi.fn(async () => {
    token = null;
  });
  const welcome = vi.fn();
  const client = new WsClient({
    url: "ws://localhost:8000/extension/ws",
    version: "0.1.0",
    makeSocket: (url) => {
      const socket = new FakeSocket(url);
      sockets.push(socket);
      return socket;
    },
    getToken: async () => token,
    onRevoked: revoked,
    onState: (state) => states.push(state),
    onWelcome: welcome,
    onTask: (task) => tasks.push(task),
    random: () => 0.5,
    ...overrides,
  });
  return { client, sockets, states, tasks, revoked, welcome };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

async function connected(env: ReturnType<typeof setup>): Promise<FakeSocket> {
  await env.client.ensure();
  const socket = env.sockets.at(-1)!;
  socket.open();
  socket.receive({ type: "welcome", device_id: "d1", heartbeat_seconds: 20 });
  return socket;
}

describe("WebSocket-клиент", () => {
  it("первым сообщением — hello с токеном; токена нет в URL", async () => {
    const env = setup();
    const socket = await connected(env);

    expect(socket.url).not.toContain(TOKEN);
    expect(socket.messages()[0]).toEqual({ type: "hello", token: TOKEN, version: "0.1.0" });
    expect(env.states.at(-1)).toEqual({ status: "online", deviceId: "d1" });
    expect(env.welcome).toHaveBeenCalledOnce();
  });

  it("без токена не подключается", async () => {
    const env = setup({ getToken: async () => null });

    await env.client.ensure();

    expect(env.sockets).toEqual([]);
    expect(env.states).toEqual([{ status: "unpaired" }]);
  });

  it("ping с интервалом из welcome", async () => {
    const env = setup();
    const socket = await connected(env);

    vi.advanceTimersByTime(20_000 * 2 + 10);

    expect(socket.messages().filter((m) => m.type === "ping")).toHaveLength(2);
  });

  it("задачи уходят в роутер; до welcome отправлять нельзя", async () => {
    const env = setup();
    await env.client.ensure();
    const socket = env.sockets[0]!;
    socket.open();
    expect(env.client.send({ type: "ping" })).toBe(false);

    socket.receive({ type: "welcome", device_id: "d1", heartbeat_seconds: 20 });
    socket.receive({
      type: "task",
      task_id: "t1",
      op: "diagnostics.echo",
      args: { nonce: "n" },
      deadline: 1,
      idempotency_key: null,
    });

    expect(env.tasks.map((t) => t.task_id)).toEqual(["t1"]);
    expect(env.client.send({ type: "ping" })).toBe(true);
  });

  it("обрыв — переподключение с растущей задержкой", async () => {
    const env = setup();
    await connected(env);

    env.sockets[0]!.serverClose(1006);
    expect(env.states.at(-1)).toMatchObject({ status: "offline" });
    await vi.advanceTimersByTimeAsync(backoffDelay(0, () => 0.5));
    expect(env.sockets).toHaveLength(2);

    env.sockets[1]!.serverClose(1006);
    await vi.advanceTimersByTimeAsync(backoffDelay(0, () => 0.5));
    expect(env.sockets).toHaveLength(2); // вторая задержка — уже больше
    await vi.advanceTimersByTimeAsync(backoffDelay(1, () => 0.5));
    expect(env.sockets).toHaveLength(3);
  });

  it("задержка растёт до минуты и не больше, с разбросом ±20 %", () => {
    expect(backoffDelay(0, () => 0.5)).toBe(1000);
    expect(backoffDelay(3, () => 0.5)).toBe(8000);
    expect(backoffDelay(20, () => 0.5)).toBe(60_000);
    expect(backoffDelay(20, () => 1)).toBe(72_000);
    expect(backoffDelay(20, () => 0)).toBe(48_000);
  });

  it("4401 — токен отозван: забываем привязку и не переподключаемся", async () => {
    const env = setup();
    await connected(env);

    env.sockets[0]!.serverClose(CLOSE_UNAUTHORIZED);
    await vi.advanceTimersByTimeAsync(1);
    await vi.advanceTimersByTimeAsync(120_000);
    await env.client.ensure(); // alarm keepalive

    expect(env.revoked).toHaveBeenCalledOnce();
    expect(env.states.at(-1)).toEqual({ status: "unpaired" });
    expect(env.sockets).toHaveLength(1);
  });

  it("4403 — сервер не принимает расширение: без переподключений", async () => {
    const env = setup();
    await connected(env);

    env.sockets[0]!.serverClose(CLOSE_BAD_ORIGIN);
    await vi.advanceTimersByTimeAsync(120_000);
    await env.client.ensure();

    expect(env.states.at(-1)).toEqual({ status: "rejected" });
    expect(env.sockets).toHaveLength(1);
  });

  it("restart после привязки — новое соединение сразу", async () => {
    const env = setup();
    await connected(env);

    await env.client.restart();

    expect(env.sockets[0]!.closedWith).toBe(1000);
    expect(env.sockets).toHaveLength(2);
  });

  it("неизвестное сообщение сервера игнорируется", async () => {
    const env = setup();
    const socket = await connected(env);

    socket.onmessage?.({ data: JSON.stringify({ type: "task", op: "x", eval: "alert(1)" }) });

    expect(env.tasks).toEqual([]);
  });
});
