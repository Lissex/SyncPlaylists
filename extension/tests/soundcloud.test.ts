// SoundCloud через расширение (4c-3): функция страницы с подменённым fetch и cookie
// (запросы, проекция ответа, ошибки, сверка аккаунта) и роутер поверх неё (темп записи,
// сколько раз ходим в /me, сообщения о состоянии площадки).

import { fakeBrowser } from "wxt/testing/fake-browser";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Journal } from "../src/background/journal";
import { Outbox } from "../src/background/outbox";
import { buildRegistry, type PageCall, type PageOutcome } from "../src/background/registry";
import { type RouterDeps, TaskRouter } from "../src/background/router";
import { soundcloudPage } from "../src/background/soundcloud";
import type { ResultMessage, TaskMessage } from "../src/protocol";

const API = "https://api-v2.soundcloud.com";
const ME = "900001";
const LEGACY_TOKEN = `2-290001-${ME}-AbCdEf`;

interface Seen {
  method: string;
  url: URL;
  headers: Record<string, string>;
  body: unknown;
}

type Reply = { status?: number; json?: unknown; text?: string; headers?: Record<string, string> };

// fetch страницы: ответы по "METHOD /path", все запросы — в seen.
function stubFetch(routes: Record<string, Reply | ((url: URL) => Reply)>): Seen[] {
  const seen: Seen[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init: RequestInit = {}) => {
      const url = new URL(input);
      const method = init.method ?? "GET";
      seen.push({
        method,
        url,
        headers: (init.headers ?? {}) as Record<string, string>,
        body: typeof init.body === "string" ? JSON.parse(init.body) : undefined,
      });
      const route = routes[`${method} ${url.pathname}`];
      const reply = typeof route === "function" ? route(url) : route;
      if (!reply) return new Response("", { status: 404 });
      const body = reply.text ?? (reply.json === undefined ? "" : JSON.stringify(reply.json));
      return new Response(body, { status: reply.status ?? 200, headers: reply.headers });
    }),
  );
  return seen;
}

function signIn(token: string | null = LEGACY_TOKEN): void {
  const cookie = token ? `sc_anonymous_id=1; oauth_token=${encodeURIComponent(token)}` : "a=1";
  vi.stubGlobal("document", { cookie });
}

function call(op: string, args: Record<string, unknown> = {}, extra: Partial<PageCall> = {}) {
  return soundcloudPage({ op, args, account: ME, verify: "cookie", ...extra });
}

const RAW_TRACK = {
  id: 11,
  kind: "track",
  title: "Starboy",
  duration: 30000,
  full_duration: 230000,
  policy: "SNIP",
  artwork_url: "https://i1.sndcdn.com/artworks-11-large.jpg",
  permalink_url: "https://soundcloud.com/weeknd/starboy",
  media: { transcodings: [{ url: "https://api-v2.soundcloud.com/media/x" }] },
  track_authorization: "secret-auth-value",
  user: { id: 77, username: "The Weeknd", verified: true, urn: "soundcloud:users:77" },
  publisher_metadata: { artist: "The Weeknd", isrc: "USUG11600976", contains_music: true },
};

beforeEach(() => {
  fakeBrowser.reset();
  signIn();
});

describe("страница SoundCloud", () => {
  it("без cookie сессии — logged_out, запросов нет", async () => {
    signIn(null);
    const seen = stubFetch({});

    expect(await call("whoami")).toEqual({ ok: false, code: "logged_out" });
    expect(seen).toEqual([]);
  });

  it("создание сета: приватный, токен сайта в заголовке, ответ — проекция", async () => {
    const seen = stubFetch({
      "POST /playlists": {
        status: 201,
        json: { id: 5100, secret_token: "s-NeW999", user: { id: 900001 }, uri: "x" },
      },
    });

    const outcome = await call("create_playlist", { title: "Лайки", description: null });

    expect(outcome).toEqual({ ok: true, data: { id: 5100, secret: "s-NeW999" } });
    expect(seen).toHaveLength(1); // аккаунт сверен по cookie, /me не запрашивали
    expect(seen[0]!.headers.Authorization).toBe(`OAuth ${LEGACY_TOKEN}`);
    expect(seen[0]!.body).toEqual({ playlist: { title: "Лайки", sharing: "private", tracks: [] } });
  });

  it("запись под другим аккаунтом — session_mismatch без запросов", async () => {
    signIn(`2-290001-900002-zz`);
    const seen = stubFetch({});

    expect(await call("like", { track_id: 11 })).toEqual({
      ok: false,
      code: "session_mismatch",
      account: "900002",
    });
    expect(seen).toEqual([]);
  });

  it("аккаунт не передан (null стал undefined в executeScript) — не сверяем", async () => {
    stubFetch({ "GET /me": { json: { id: 900001, username: "Me" } } });

    const outcome = await soundcloudPage({
      op: "whoami",
      args: {},
      account: undefined as unknown as null,
      verify: "whoami",
    });

    expect(outcome).toMatchObject({ ok: true, data: { id: 900001 } });
  });

  it("JWT: аккаунт — из claim sub", async () => {
    const claims = btoa(JSON.stringify({ sub: "soundcloud:users:900002", exp: 1 }));
    signIn(`h.${claims.replace(/=+$/, "")}.sig`);
    stubFetch({});

    expect(await call("like", { track_id: 11 })).toMatchObject({ code: "session_mismatch" });
  });

  it("чтение с полной сверкой: сначала /me, аккаунт возвращается для кэша", async () => {
    const seen = stubFetch({
      "GET /me": { json: { id: 900001, username: "Listener", email: "x@y" } },
      [`GET /users/${ME}/track_likes`]: { json: { collection: [], next_href: null } },
    });

    const outcome = await call(
      "liked_tracks_page",
      { cursor: null, limit: 2 },
      { verify: "whoami" },
    );

    expect(outcome).toEqual({ ok: true, data: { tracks: [], next_cursor: null }, account: ME });
    expect(seen.map((s) => s.url.pathname)).toEqual(["/me", `/users/${ME}/track_likes`]);
  });

  it("лайки: только нужные поля трека, курсор — query next_href без client_id", async () => {
    stubFetch({
      [`GET /users/${ME}/track_likes`]: {
        json: {
          collection: [{ created_at: "x", track: RAW_TRACK }, { track: null }],
          next_href: `${API}/users/${ME}/track_likes?offset=2024-01&limit=2&client_id=CID`,
        },
      },
    });

    const outcome = (await call("liked_tracks_page", { cursor: null, limit: 2 })) as {
      ok: true;
      data: { tracks: unknown[]; next_cursor: string };
    };

    expect(outcome.data.tracks).toEqual([
      {
        id: 11,
        kind: "track",
        title: "Starboy",
        duration: 30000,
        full_duration: 230000,
        policy: "SNIP",
        artwork_url: "https://i1.sndcdn.com/artworks-11-large.jpg",
        user: { id: 77, username: "The Weeknd", verified: true },
        publisher_metadata: { artist: "The Weeknd", isrc: "USUG11600976" },
      },
    ]);
    expect(outcome.data.next_cursor).toBe("offset=2024-01");
  });

  it("курсор применяется к запросу следующей страницы", async () => {
    const seen = stubFetch({
      [`GET /users/${ME}/track_likes`]: { json: { collection: [] } },
    });

    await call("liked_tracks_page", { cursor: "offset=2024-01", limit: 2 });

    expect(seen[0]!.url.searchParams.get("offset")).toBe("2024-01");
    expect(seen[0]!.url.searchParams.get("limit")).toBe("2");
  });

  it("next_href на другой путь — unavailable (курсор не уводит запросы)", async () => {
    stubFetch({
      [`GET /users/${ME}/track_likes`]: {
        json: { collection: [], next_href: `${API}/me/followings?offset=1` },
      },
    });

    expect(await call("liked_tracks_page", { cursor: null, limit: 2 })).toMatchObject({
      ok: false,
      code: "unavailable",
    });
  });

  it("приватный сет: секрет в запросе, на проводе — secret", async () => {
    const seen = stubFetch({
      "GET /playlists/5100": {
        json: {
          id: 5100,
          kind: "playlist",
          title: "Лайки",
          user_id: 900001,
          secret_token: "s-NeW999",
          sharing: "private",
          tracks: [RAW_TRACK, { id: 12, kind: "track", policy: "ALLOW", monetization_model: "x" }],
        },
      },
    });

    const outcome = (await call("playlist", { playlist_id: 5100, secret: "s-NeW999" })) as {
      ok: true;
      data: Record<string, unknown>;
    };

    expect(seen[0]!.url.searchParams.get("secret_token")).toBe("s-NeW999");
    expect(outcome.data).toMatchObject({ id: 5100, secret: "s-NeW999", user_id: 900001 });
    expect(outcome.data).not.toHaveProperty("secret_token");
    expect(outcome.data.tracks).toEqual([
      expect.objectContaining({ id: 11, title: "Starboy" }),
      { id: 12, kind: "track", policy: "ALLOW" },
    ]);
  });

  it("замена треков сета и лайк — нужные методы и пути", async () => {
    const seen = stubFetch({
      "PUT /playlists/5100": { json: { id: 5100 } },
      [`PUT /users/${ME}/track_likes/11`]: { text: "" },
    });

    expect(
      await call("set_playlist_tracks", { playlist_id: 5100, secret: "s-1", track_ids: [1, 2] }),
    ).toEqual({ ok: true, data: {} });
    expect(await call("like", { track_id: 11 })).toEqual({ ok: true, data: {} });

    expect(seen[0]!.body).toEqual({ playlist: { tracks: [1, 2] } });
    expect(seen[0]!.url.searchParams.get("secret_token")).toBe("s-1");
    expect(seen[1]!.method).toBe("PUT");
  });

  it.each([
    [{ status: 403, headers: { "x-datadome": "protected" }, text: "{}" }, "captcha"],
    [
      { status: 403, headers: { "content-type": "text/html" }, text: "geo.captcha-delivery.com" },
      "captcha",
    ],
    [{ status: 403, json: { errors: [] } }, "not_writable"],
    [{ status: 401 }, "logged_out"],
    [{ status: 404 }, "not_found"],
    [{ status: 422 }, "not_writable"],
    [{ status: 503 }, "unavailable"],
  ] as Array<[Reply, string]>)("HTTP %j → %s", async (reply, code) => {
    stubFetch({ "POST /playlists": reply });

    const outcome = await call("create_playlist", { title: "x", description: null });

    expect(outcome).toMatchObject({ ok: false, code });
    expect(Object.keys(outcome).sort()).toEqual(
      code === "unavailable" ? ["code", "detail", "ok"] : ["code", "ok"],
    );
  });

  it("429 — rate_limited со сроком из Retry-After", async () => {
    stubFetch({
      [`PUT /users/${ME}/track_likes/11`]: { status: 429, headers: { "retry-after": "7" } },
    });

    expect(await call("like", { track_id: 11 })).toEqual({
      ok: false,
      code: "rate_limited",
      retry_after: 7,
    });
  });
});

// ------------------------------------------------------------------ роутер + страница

function task(op: string, args: Record<string, unknown>, extra: Partial<TaskMessage> = {}) {
  return {
    type: "task" as const,
    task_id: `t-${Math.random()}`,
    op: `soundcloud.${op}`,
    args,
    deadline: Date.now() / 1000 + 600,
    idempotency_key: null,
    account: ME,
    ...extra,
  };
}

function router(overrides: Partial<RouterDeps> = {}) {
  let clock = 1_000_000;
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

describe("роутер SoundCloud", () => {
  it("10 лайков подряд: /me не запрашивается, между записями ≥ 1 с", async () => {
    const { router: r, sent, time } = router();
    const at: number[] = [];
    const seen = stubFetch({
      "GET /me": { json: { id: 900001 } },
      [`PUT /users/${ME}/track_likes/1`]: () => {
        at.push(time());
        return { text: "" };
      },
    });
    // Все лайки — один путь (track 1): нам важны темп и число запросов /me.
    for (let i = 0; i < 10; i++) await r.handle(task("like", { track_id: 1 }));

    expect(sent.every((m) => m.ok)).toBe(true);
    expect(seen.filter((s) => s.url.pathname === "/me").length).toBeLessThanOrEqual(1);
    expect(at).toHaveLength(10);
    for (let i = 1; i < at.length; i++) expect(at[i]! - at[i - 1]!).toBeGreaterThanOrEqual(1000);
  });

  it("чтения: полный /me не чаще раза в 60 с", async () => {
    const { router: r, advance } = router();
    const seen = stubFetch({
      "GET /me": { json: { id: 900001 } },
      [`GET /users/${ME}/track_likes`]: { json: { collection: [] } },
    });
    const read = () => r.handle(task("liked_tracks_page", { cursor: null, limit: 2 }));

    await read();
    await read();
    advance(61_000);
    await read();

    expect(seen.filter((s) => s.url.pathname === "/me")).toHaveLength(2);
  });

  it("в браузере другой аккаунт: session_mismatch и сервер узнаёт новый аккаунт", async () => {
    signIn("2-290001-900002-zz");
    stubFetch({});
    const { router: r, sent, states } = router();

    await r.handle(task("like", { track_id: 1 }));

    expect(sent[0]).toMatchObject({ ok: false, error: { code: "session_mismatch" } });
    expect(states).toEqual([["soundcloud", "ok", "900002"]]);
  });

  it("капча на записи: captcha в ответе и в состоянии площадки", async () => {
    stubFetch({ "POST /playlists": { status: 403, headers: { "x-datadome": "1" } } });
    const { router: r, sent, states } = router();

    await r.handle(task("create_playlist", { title: "x", description: null }));

    expect(sent[0]).toMatchObject({ ok: false, error: { code: "captcha" } });
    expect(states).toEqual([["soundcloud", "captcha", null]]);
  });

  it("429: retry_after доходит до сервера", async () => {
    stubFetch({
      [`PUT /users/${ME}/track_likes/1`]: { status: 429, headers: { "retry-after": "5" } },
    });
    const { router: r, sent } = router();

    await r.handle(task("like", { track_id: 1 }));

    expect(sent[0]).toMatchObject({ ok: false, error: { code: "rate_limited", retry_after: 5 } });
  });

  it("повтор создания с тем же ключом — второй POST не уходит", async () => {
    const seen = stubFetch({
      "POST /playlists": { status: 201, json: { id: 1, secret_token: "s-a" } },
    });
    const { router: r, sent } = router();
    const create = (id: string) =>
      r.handle(
        task(
          "create_playlist",
          { title: "x", description: null },
          { task_id: id, idempotency_key: "create_playlist:r1" },
        ),
      );

    await create("a");
    await create("b");

    expect(seen.filter((s) => s.method === "POST")).toHaveLength(1);
    expect(sent.map((m) => m.data)).toEqual([
      { id: 1, secret: "s-a" },
      { id: 1, secret: "s-a" },
    ]);
  });

  it("аргументы не по схеме (чужой путь вместо id) — bad_args, в страницу не идём", async () => {
    const seen = stubFetch({});
    const { router: r, sent } = router();

    await r.handle(task("playlist", { playlist_id: "../me", secret: "s-1" }));
    await r.handle(task("liked_tracks_page", { cursor: "https://evil.example/x", limit: 2 }));

    expect(seen).toEqual([]);
    expect(sent.map((m) => m.error?.code)).toEqual(["bad_args", "bad_args"]);
  });

  it("probe whoami: кто вошёл, без открытия вкладки при existingOnly", async () => {
    stubFetch({ "GET /me": { json: { id: 900001, username: "Listener", email: "x@y" } } });
    const opened: boolean[] = [];
    const { router: r } = router({
      runInTarget: async (_t, main, input, existingOnly) => {
        opened.push(Boolean(existingOnly));
        return (main as (a: unknown) => unknown)(input);
      },
    });

    expect(await r.probe("soundcloud.whoami", true)).toEqual({
      ok: true,
      data: { id: 900001, username: "Listener" },
    });
    expect(opened).toEqual([true]);
  });

  it("probe: страница не отвечает — понятная ошибка по таймауту, а не вечное ожидание", async () => {
    const { router: r } = router({
      probeTimeoutMs: 10,
      runInTarget: () => new Promise(() => undefined),
    });

    expect(await r.probe("soundcloud.whoami")).toMatchObject({
      ok: false,
      code: "unavailable",
      detail: expect.stringContaining("timeout"),
    });
  });

  it("probe без вкладки — null (состояние не меняем)", async () => {
    const { router: r } = router({
      runInTarget: async () => {
        throw new Error("no_tab");
      },
    });

    expect(await r.probe("soundcloud.whoami", true)).toBeNull();
  });
});

// Проверка типов: функция страницы возвращает PageOutcome.
const _typed: (call: PageCall) => Promise<PageOutcome> = soundcloudPage;
void _typed;
