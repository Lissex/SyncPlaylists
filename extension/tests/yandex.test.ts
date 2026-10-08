// Яндекс Музыка через расширение (4c-4): функция страницы с подменённым fetch и cookie
// (запросы только с cookie сессии, проекция ответа, ошибки и капча, сверка аккаунта по
// yandex_login) и роутер поверх неё (темп, сколько раз ходим в account/status, состояние
// площадки). Плюс правило 11h: в функции страницы нет обращений к токену.

import vm from "node:vm";

import { fakeBrowser } from "wxt/testing/fake-browser";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Journal } from "../src/background/journal";
import { Outbox } from "../src/background/outbox";
import { buildRegistry, type PageCall, type PageOutcome } from "../src/background/registry";
import { type RouterDeps, TaskRouter } from "../src/background/router";
import { yandexPage } from "../src/background/yandex";
import type { ResultMessage, TaskMessage } from "../src/protocol";

const API = "https://api.music.yandex.ru";
const UID = "123456789";
const LOGIN = "test.user";

interface Seen {
  method: string;
  url: URL;
  init: RequestInit;
  form: Record<string, string>;
}

type Reply = {
  status?: number;
  json?: unknown;
  text?: string;
  headers?: Record<string, string>;
};

// fetch страницы: ответы по "METHOD /path", все запросы — в seen.
function stubFetch(routes: Record<string, Reply | ((seen: Seen) => Reply)>): Seen[] {
  const seen: Seen[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init: RequestInit = {}) => {
      const url = new URL(input);
      const method = init.method ?? "GET";
      const form = Object.fromEntries(new URLSearchParams(String(init.body ?? "")));
      const entry = { method, url, init, form };
      seen.push(entry);
      const route = routes[`${method} ${url.pathname}`];
      const reply = typeof route === "function" ? route(entry) : route;
      if (!reply) return new Response("", { status: 404 });
      const body =
        reply.text ?? (reply.json === undefined ? "" : JSON.stringify({ result: reply.json }));
      const headers = { "content-type": "application/json; charset=utf-8", ...reply.headers };
      return new Response(body, { status: reply.status ?? 200, headers });
    }),
  );
  return seen;
}

function signIn(login: string | null = LOGIN): void {
  const cookie = login ? `yandexuid=1; yandex_login=${login}; i=x` : "yandexuid=1";
  vi.stubGlobal("document", { cookie });
}

function call(op: string, args: Record<string, unknown> = {}, extra: Partial<PageCall> = {}) {
  return yandexPage({
    op,
    args,
    account: UID,
    verify: "cookie",
    account_hint: LOGIN,
    ...extra,
  });
}

const STATUS = {
  account: {
    now: "2026-10-08T00:00:00+00:00",
    uid: 123456789,
    login: LOGIN,
    displayName: "Test User",
    birthday: "1990-01-01",
    passport_phones: [{ phone: "+7900" }],
  },
  permissions: { values: ["landing-play"] },
  plus: { hasPlus: true },
};

const RAW_TRACK = {
  id: "57703",
  realId: "57703",
  title: "Starboy",
  version: "Live",
  available: true,
  durationMs: 260000,
  coverUri: "avatars.yandex.net/get-music-content/49876/x/%%",
  trackSource: "OWN",
  lyricsInfo: { hasAvailableSyncLyrics: true },
  downloadInfo: { token: "secret" },
  artists: [{ id: 4611844, name: "The Weeknd", cover: { uri: "x" } }],
  albums: [{ id: 4766, title: "Starboy", year: 2016 }],
};

const TRACK = {
  id: "57703",
  title: "Starboy",
  version: "Live",
  available: true,
  durationMs: 260000,
  coverUri: "avatars.yandex.net/get-music-content/49876/x/%%",
  artists: [{ id: "4611844", name: "The Weeknd" }],
  albums: [{ id: "4766" }],
};

function playlist(count: number, revision = 7): Record<string, unknown> {
  return {
    kind: 1001,
    uid: 123456789,
    owner: { uid: 123456789, login: LOGIN, name: LOGIN, sex: "unknown" },
    playlistUuid: "lk.x",
    title: "Дорога",
    description: "Для машины",
    revision,
    trackCount: count,
    visibility: "private",
    tracks: Array.from({ length: count }, (_, i) => ({
      id: 100 + i,
      albumId: 200 + i,
      timestamp: "2026-09-01T10:00:00+00:00",
      originalIndex: i,
      track: RAW_TRACK,
    })),
  };
}

beforeEach(() => {
  fakeBrowser.reset();
  signIn();
});

describe("страница Яндекса: запросы и проекция", () => {
  it("whoami: только cookie (credentials include), без токена и заголовков сайта", async () => {
    const seen = stubFetch({ "GET /account/status": { json: STATUS } });

    const outcome = await call("whoami", {}, { account: null, account_hint: null });

    expect(outcome).toEqual({
      ok: true,
      data: { id: UID, username: "Test User", login: LOGIN },
      account: UID,
      hint: LOGIN,
    });
    expect(seen).toHaveLength(1);
    const { url, init } = seen[0]!;
    expect(url.origin).toBe(API);
    expect(init.credentials).toBe("include");
    expect(init.redirect).toBe("manual");
    const headers = Object.keys(init.headers as Record<string, string>).map((h) => h.toLowerCase());
    expect(headers).not.toContain("authorization");
    expect(headers.filter((h) => h.startsWith("x-yandex-music"))).toEqual([]);
  });

  it("вход не выполнен: анонимный account/status — logged_out", async () => {
    stubFetch({
      "GET /account/status": { json: { account: { now: "x", serviceAvailable: true } } },
    });

    expect(await call("whoami", {}, { account: null })).toEqual({ ok: false, code: "logged_out" });
  });

  it("поиск: проекция трека — только то, что читает сервер", async () => {
    const seen = stubFetch({
      "GET /search": { json: { tracks: { results: [RAW_TRACK, { title: "без id" }] } } },
    });

    const outcome = await call("search", { text: "The Weeknd Starboy" });

    expect(outcome).toEqual({ ok: true, data: { tracks: [TRACK] } });
    expect(Object.fromEntries(seen[0]!.url.searchParams)).toEqual({
      text: "The Weeknd Starboy",
      type: "track",
      page: "0",
    });
  });

  it("tracks: POST формой track-ids", async () => {
    const seen = stubFetch({ "POST /tracks": { json: [RAW_TRACK] } });

    const outcome = await call("tracks", { ids: ["57703:4766", "999001"] });

    expect(outcome).toEqual({ ok: true, data: { tracks: [TRACK] } });
    expect(seen[0]!.form).toEqual({ "track-ids": "57703:4766,999001", "with-positions": "True" });
    expect((seen[0]!.init.headers as Record<string, string>)["Content-Type"]).toBe(
      "application/x-www-form-urlencoded",
    );
  });

  it("playlist_page: заголовок и срез записей без вложенных треков", async () => {
    stubFetch({ [`GET /users/${LOGIN}/playlists/1001`]: { json: playlist(5) } });
    const args = { owner: LOGIN, kind: 1001, uuid: null, offset: 2, limit: 2, revision: 7 };

    const outcome = await call("playlist_page", args);

    expect(outcome).toEqual({
      ok: true,
      data: {
        kind: 1001,
        uid: UID,
        owner: { uid: UID, login: LOGIN },
        title: "Дорога",
        description: "Для машины",
        revision: 7,
        trackCount: 5,
        tracks: [
          { id: "102", albumId: "202", timestamp: "2026-09-01T10:00:00+00:00" },
          { id: "103", albumId: "203", timestamp: "2026-09-01T10:00:00+00:00" },
        ],
        nextOffset: 4,
      },
    });
  });

  it("playlist_page по uuid; последняя страница — без nextOffset", async () => {
    stubFetch({ "GET /playlist/11111111-2222": { json: playlist(3) } });
    const args = {
      owner: null,
      kind: null,
      uuid: "11111111-2222",
      offset: 2,
      limit: 2,
      revision: null,
    };

    const outcome = await call("playlist_page", args);

    expect(outcome.ok && (outcome.data as Record<string, unknown>).nextOffset).toBeUndefined();
  });

  it("страница другой ревизии, чем первая, — conflict", async () => {
    stubFetch({
      [`GET /users/${UID}/likes/tracks`]: { json: { library: { revision: 9, tracks: [] } } },
    });

    expect(await call("library_ids_page", { offset: 5000, limit: 5000, revision: 8 })).toEqual({
      ok: false,
      code: "conflict",
    });
  });

  it("library_ids_page: id лайков страницами по uid задачи", async () => {
    stubFetch({
      [`GET /users/${UID}/likes/tracks`]: {
        json: {
          library: {
            uid: 123456789,
            revision: 4242,
            playlistUuid: "lk.x",
            tracks: [
              { id: "1", albumId: "10", timestamp: "t1" },
              { id: "2", timestamp: "t2" },
              { id: "3", albumId: "30", timestamp: "t3" },
            ],
          },
        },
      },
    });

    expect(await call("library_ids_page", { offset: 0, limit: 2, revision: null })).toEqual({
      ok: true,
      data: {
        revision: 4242,
        tracks: [
          { id: "1", albumId: "10", timestamp: "t1" },
          { id: "2", timestamp: "t2" },
        ],
        nextOffset: 2,
      },
    });
  });
});

describe("страница Яндекса: запись", () => {
  it("создание: приватный, по uid задачи, аккаунт сверен по cookie без запроса", async () => {
    const seen = stubFetch({
      [`POST /users/${UID}/playlists/create`]: { json: { ...playlist(0, 1), kind: 1077 } },
    });

    const outcome = await call("create_playlist", { title: "Лайки SoundCloud" });

    expect(outcome).toEqual({ ok: true, data: { kind: 1077, revision: 1 } });
    expect(seen).toHaveLength(1);
    expect(seen[0]!.form).toEqual({ title: "Лайки SoundCloud", visibility: "private" });
  });

  it("add_tracks: вставка change-relative с ревизией; трек без альбома — без albumId", async () => {
    const seen = stubFetch({
      [`POST /users/${UID}/playlists/1001/change-relative`]: { json: playlist(6, 8) },
    });
    const tracks = [
      { id: "424242", album_id: "4242" },
      { id: "700", album_id: null },
    ];

    const outcome = await call("add_tracks", { kind: 1001, revision: 7, at: 4, tracks });

    expect(outcome).toEqual({ ok: true, data: { revision: 8, trackCount: 6 } });
    expect(seen[0]!.form.revision).toBe("7");
    expect(JSON.parse(seen[0]!.form.diff!)).toEqual([
      { op: "insert", at: 4, tracks: [{ id: "424242", albumId: "4242" }, { id: "700" }] },
    ]);
  });

  it.each([
    [412, { error: { name: "wrong-revision" } }, "conflict"],
    [400, { error: { name: "validation" } }, "bad_request"],
    [403, { error: { name: "forbidden" } }, "not_writable"],
    [401, {}, "logged_out"],
    [404, {}, "not_found"],
  ])("ответ %s при записи — %s", async (status, body, code) => {
    stubFetch({
      [`POST /users/${UID}/playlists/1001/change-relative`]: { status, text: JSON.stringify(body) },
    });

    const outcome = await call("add_tracks", {
      kind: 1001,
      revision: 7,
      at: 0,
      tracks: [{ id: "1", album_id: "2" }],
    });

    expect(outcome).toMatchObject({ ok: false, code });
  });

  it("like_many: add-multiple формой", async () => {
    const seen = stubFetch({
      [`POST /users/${UID}/likes/tracks/add-multiple`]: { json: { revision: 5 } },
    });

    expect(await call("like_many", { ids: ["3:30", "1:10"] })).toEqual({ ok: true, data: {} });
    expect(seen[0]!.form).toEqual({ "track-ids": "3:30,1:10" });
  });
});

describe("страница Яндекса: капча и ошибки", () => {
  const html = "<!DOCTYPE html><html><body>showcaptcha</body></html>";

  it.each<[string, Reply]>([
    ["429 + x-yandex-captcha", { status: 429, text: html, headers: { "x-yandex-captcha": "429" } }],
    [
      "429 HTML без заголовка (CORS)",
      { status: 429, text: html, headers: { "content-type": "text/html" } },
    ],
    ["200 HTML вместо JSON", { status: 200, text: html, headers: { "content-type": "text/html" } }],
    [
      "редирект на капчу",
      { status: 302, text: "", headers: { location: "https://x/showcaptcha" } },
    ],
  ])("%s — captcha", async (_name, reply) => {
    stubFetch({ "GET /search": reply });

    expect(await call("search", { text: "x" })).toMatchObject({ ok: false, code: "captcha" });
  });

  it("429 без признаков капчи — rate_limited с Retry-After", async () => {
    stubFetch({
      "GET /search": { status: 429, text: "{}", headers: { "retry-after": "30" } },
    });

    expect(await call("search", { text: "x" })).toMatchObject({
      ok: false,
      code: "rate_limited",
      retry_after: 30,
    });
  });

  it("сеть упала — unavailable с причиной только для окна расширения", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );

    expect(await call("search", { text: "x" })).toMatchObject({
      ok: false,
      code: "unavailable",
      detail: expect.stringContaining("Failed to fetch"),
    });
  });
});

describe("страница Яндекса: сверка аккаунта", () => {
  it("в cookie другой логин — полный account/status и session_mismatch с фактическим uid", async () => {
    signIn("other.user");
    const seen = stubFetch({
      "GET /account/status": {
        json: { account: { ...STATUS.account, uid: 987654321, login: "other.user" } },
      },
    });

    expect(await call("like_many", { ids: ["1:10"] })).toEqual({
      ok: false,
      code: "session_mismatch",
      account: "987654321",
    });
    expect(seen.map((s) => s.url.pathname)).toEqual(["/account/status"]);
  });

  it("подсказки нет (или нет cookie) — один account/status, в ответе uid и логин", async () => {
    signIn(null);
    const seen = stubFetch({
      "GET /account/status": { json: STATUS },
      [`POST /users/${UID}/likes/tracks/add-multiple`]: { json: {} },
    });

    expect(await call("like_many", { ids: ["1:10"] }, { account_hint: null })).toEqual({
      ok: true,
      data: {},
      account: UID,
      hint: LOGIN,
    });
    expect(seen.map((s) => s.url.pathname)).toEqual([
      "/account/status",
      `/users/${UID}/likes/tracks/add-multiple`,
    ]);
  });

  it("аккаунт не передан (null стал undefined в executeScript) — не сверяем", async () => {
    stubFetch({ "GET /account/status": { json: STATUS } });

    const outcome = await yandexPage({
      op: "whoami",
      args: {},
      account: undefined as unknown as null,
      verify: "whoami",
    });

    expect(outcome).toMatchObject({ ok: true, account: UID });
  });
});

describe("правило 11h: токен Яндекса не читаем", () => {
  const source = yandexPage.toString();

  it.each([
    "localStorage",
    "sessionStorage",
    "Authorization",
    "OAuth",
    "x-yandex-music",
    "Session_id",
  ])("в функции страницы нет %s", (needle) => {
    expect(source).not.toContain(needle);
  });

  it("функция самодостаточна: выполняется в пустом контексте без хелперов бандлера", async () => {
    const context = vm.createContext({
      document: { cookie: `yandex_login=${LOGIN}` },
      fetch: async () =>
        new Response(JSON.stringify({ result: STATUS }), {
          headers: { "content-type": "application/json" },
        }),
      URL,
      URLSearchParams,
      AbortController,
      setTimeout,
      clearTimeout,
      JSON,
      call: { op: "whoami", args: {}, account: null, verify: "whoami" },
    });

    const outcome = await vm.runInContext(`(${source})(call)`, context);

    expect(outcome).toMatchObject({ ok: true, account: UID });
  });
});

// ------------------------------------------------------------------ роутер

function task(op: string, args: Record<string, unknown>, extra: Partial<TaskMessage> = {}) {
  return {
    type: "task" as const,
    task_id: `t-${Math.random()}`,
    op: `yandex.${op}`,
    args,
    deadline: Date.now() / 1000 + 600,
    idempotency_key: null,
    account: UID,
    ...extra,
  };
}

function router(overrides: Partial<RouterDeps> = {}) {
  let clock = Date.now();
  const sent: ResultMessage[] = [];
  const states: Array<[string, string, string | null]> = [];
  const deps: RouterDeps = {
    registry: buildRegistry({ apiBase: "http://localhost:8000", diagnostics: false }),
    journal: new Journal(() => clock),
    outbox: new Outbox(),
    hasPermission: async () => true,
    runInTarget: async (_target, main, input) => (main as (a: unknown) => unknown)(input),
    sendResult: (message) => {
      sent.push(message);
      return true;
    },
    sendProgress: () => undefined,
    reportState: (key, session, account) => states.push([key, session, account]),
    now: () => clock,
    sleep: async (ms) => {
      clock += ms;
    },
    ...overrides,
  };
  return {
    router: new TaskRouter(deps),
    sent,
    states,
    advance: (ms: number) => {
      clock += ms;
    },
    time: () => clock,
  };
}

describe("роутер Яндекса", () => {
  it("10 пачек подряд: account/status один раз, дальше сверка по cookie", async () => {
    const { router: r, sent } = router();
    const seen = stubFetch({
      "GET /account/status": { json: STATUS },
      [`POST /users/${UID}/likes/tracks/add-multiple`]: { json: {} },
    });

    for (let i = 0; i < 10; i++) await r.handle(task("like_many", { ids: [`${i}:1`] }));

    expect(sent.every((m) => m.ok)).toBe(true);
    expect(seen.filter((s) => s.url.pathname === "/account/status")).toHaveLength(1);
  });

  it("любая операция Яндекса — не чаще раза в секунду (поиск тоже)", async () => {
    const { router: r, time } = router();
    const at: number[] = [];
    stubFetch({
      "GET /account/status": { json: STATUS },
      "GET /search": () => {
        at.push(time());
        return { json: { tracks: { results: [] } } };
      },
    });

    for (let i = 0; i < 5; i++) await r.handle(task("search", { text: `q${i}` }));

    expect(at).toHaveLength(5);
    for (let i = 1; i < at.length; i++) expect(at[i]! - at[i - 1]!).toBeGreaterThanOrEqual(1000);
  });

  it("капча: captcha в ответе и в состоянии площадки", async () => {
    stubFetch({
      "GET /account/status": { json: STATUS },
      "GET /search": {
        status: 429,
        text: "<html>captcha</html>",
        headers: { "x-yandex-captcha": "429" },
      },
    });
    const { router: r, sent, states } = router();

    await r.handle(task("search", { text: "x" }));

    expect(sent[0]).toMatchObject({ ok: false, error: { code: "captcha" } });
    expect(states).toEqual([["yandex", "captcha", null]]);
  });

  it.each([
    [412, "wrong-revision", "conflict"],
    [400, "validation", "bad_request"],
  ])("ответ %s (%s) доходит до сервера кодом %s", async (status, name, code) => {
    stubFetch({
      "GET /account/status": { json: STATUS },
      [`POST /users/${UID}/playlists/1001/change-relative`]: {
        status,
        text: JSON.stringify({ error: { name } }),
      },
    });
    const { router: r, sent } = router();
    const tracks = [{ id: "1", album_id: "2" }];

    await r.handle(task("add_tracks", { kind: 1001, revision: 7, at: 0, tracks }));

    expect(sent[0]).toMatchObject({ ok: false, error: { code } });
  });

  it("повтор создания с тем же ключом — второй POST не уходит", async () => {
    const seen = stubFetch({
      "GET /account/status": { json: STATUS },
      [`POST /users/${UID}/playlists/create`]: { json: { kind: 1077, revision: 1 } },
    });
    const { router: r, sent } = router();
    const create = (id: string) =>
      r.handle(
        task(
          "create_playlist",
          { title: "x" },
          { task_id: id, idempotency_key: "create_playlist:r1" },
        ),
      );

    await create("a");
    await create("b");

    expect(seen.filter((s) => s.method === "POST")).toHaveLength(1);
    expect(sent.map((m) => m.data)).toEqual([
      { kind: 1077, revision: 1 },
      { kind: 1077, revision: 1 },
    ]);
  });

  it("аргументы не по схеме — bad_args, в страницу не идём", async () => {
    const seen = stubFetch({});
    const { router: r, sent } = router();

    await r.handle(
      task("playlist_page", {
        owner: "../me",
        kind: 1,
        uuid: null,
        offset: 0,
        limit: 10,
        revision: null,
      }),
    );
    await r.handle(
      task("playlist_page", {
        owner: "a",
        kind: 1,
        uuid: "u",
        offset: 0,
        limit: 10,
        revision: null,
      }),
    );
    await r.handle(task("tracks", { ids: ["1;drop"] }));
    await r.handle(task("library_ids_page", { offset: 0, limit: 100000, revision: null }));

    expect(seen).toEqual([]);
    expect(sent.map((m) => m.error?.code)).toEqual([
      "bad_args",
      "bad_args",
      "bad_args",
      "bad_args",
    ]);
  });

  it("probe whoami: кто вошёл (для подключения площадки)", async () => {
    stubFetch({ "GET /account/status": { json: STATUS } });
    const { router: r } = router();

    expect(await r.probe("yandex.whoami")).toEqual({
      ok: true,
      data: { id: UID, username: "Test User", login: LOGIN },
    });
  });
});

// Проверка типов: функция страницы возвращает PageOutcome.
const _typed: (call: PageCall) => Promise<PageOutcome> = yandexPage;
void _typed;
