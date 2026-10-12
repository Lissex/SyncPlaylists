// VK Музыка через расширение (4c-5): функция страницы с подменённым fetch и localStorage
// (веб-токен страницы — только в тело запроса к VK, проекция без ссылок на аудио, коды
// ошибок, сверка аккаунта) и роутер поверх неё (истёкший токен → перезагрузка вкладки и
// один повтор, бережный темп «Моей музыки»).

import vm from "node:vm";

import { fakeBrowser } from "wxt/testing/fake-browser";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Journal } from "../src/background/journal";
import { Outbox } from "../src/background/outbox";
import { buildRegistry, type PageCall, type PageOutcome } from "../src/background/registry";
import { type RouterDeps, TaskRouter } from "../src/background/router";
import { vkPage } from "../src/background/vk";
import type { ResultMessage, TaskMessage } from "../src/protocol";

const ME = "100500";
const TOKEN_KEY = "6287487:web_token:login:auth";

interface Seen {
  method: string; // метод API VK
  query: URLSearchParams;
  form: Record<string, string>;
  init: RequestInit;
}

type Reply = {
  response?: unknown;
  error?: Record<string, unknown>;
  status?: number;
  text?: string;
};

// fetch страницы: ответы по методу API ("audio.search"), все запросы — в seen. users.get
// по умолчанию отвечает владельцем задачи (сверка аккаунта роутером).
function stubFetch(routes: Record<string, Reply | ((seen: Seen) => Reply)>): Seen[] {
  routes = { "users.get": { response: [{ id: 100500, first_name: "Me" }] }, ...routes };
  const seen: Seen[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init: RequestInit = {}) => {
      const url = new URL(input);
      const entry = {
        method: url.pathname.replace("/method/", ""),
        query: url.searchParams,
        form: Object.fromEntries(new URLSearchParams(String(init.body ?? ""))),
        init,
      };
      seen.push(entry);
      const route = routes[entry.method];
      const reply = typeof route === "function" ? route(entry) : route;
      if (!reply) return new Response("", { status: 404 });
      const body =
        reply.text ??
        JSON.stringify(reply.error ? { error: reply.error } : { response: reply.response });
      return new Response(body, { status: reply.status ?? 200 });
    }),
  );
  return seen;
}

// localStorage страницы vk.ru: запись веб-токена (JSON или голая строка) и чужие ключи.
function signIn(token: string | null = "vk1.token-1", userId: number | null = 100500): void {
  const storage: Record<string, string> = { "audio:volume": "0.5" };
  if (token !== null) {
    storage[TOKEN_KEY] = JSON.stringify(
      userId === null ? { access_token: token } : { access_token: token, user_id: userId },
    );
  }
  vi.stubGlobal("localStorage", {
    get length() {
      return Object.keys(storage).length;
    },
    key: (i: number) => Object.keys(storage)[i] ?? null,
    getItem: vi.fn((k: string) => storage[k] ?? null),
    setToken(value: string) {
      storage[TOKEN_KEY] = JSON.stringify({ access_token: value, user_id: 100500 });
    },
  });
}

function call(op: string, args: Record<string, unknown> = {}, extra: Partial<PageCall> = {}) {
  return vkPage({ op, args, account: ME, verify: "cookie", ...extra });
}

const RAW_TRACK = {
  artist: "Don Toliver",
  id: 456949008,
  owner_id: 474499302,
  title: "Call Back",
  duration: 124,
  access_key: "0a1b2c3d",
  ads: { content_id: "474499302_456949008", duration: "124" },
  is_licensed: true,
  track_code: "abc",
  url: "https://cs1.vkuseraudio.net/s/v1/a/x.mp3?extra=secret",
  date: 1,
  album: {
    id: 1,
    title: "Hardstone",
    owner_id: -2000,
    access_key: "x",
    thumb: {
      photo_300: "https://sun9-1.userapi.com/300.jpg",
      photo_600: "https://sun9-1.userapi.com/600.jpg",
    },
  },
  main_artists: [{ name: "Don Toliver", domain: "123", id: "123", is_followed: false }],
  release_audio_id: "-2001400099_99400099",
  permissions: { edit: false },
};

const TRACK = {
  owner_id: 474499302,
  id: 456949008,
  access_key: "0a1b2c3d",
  artist: "Don Toliver",
  title: "Call Back",
  duration: 124,
  album_title: "Hardstone",
  cover_url: "https://sun9-1.userapi.com/600.jpg",
  main_artists: ["Don Toliver"],
  featured_artists: [],
  release_audio_id: "-2001400099_99400099",
};

const PLAYLIST = {
  playlist: {
    id: 158,
    owner_id: 100500,
    type: "ugc",
    title: "sp-spike",
    description: "Описание",
    count: 3,
    access_key: "34350a7d",
    no_discover: true,
    permissions: { play: true, edit: true },
    photo: { photo_300: "x" },
  },
  profiles: [{ id: 100500, first_name: "Me" }],
};

beforeEach(() => {
  fakeBrowser.reset();
  signIn();
});

describe("страница VK: запросы и проекция", () => {
  it("whoami: users.get с токеном страницы в теле, client_id и v в адресе", async () => {
    const seen = stubFetch({
      "users.get": { response: [{ id: 100500, first_name: "Грачья", last_name: "А", email: "x" }] },
    });

    const outcome = await call("whoami", {}, { account: null, verify: "whoami" });

    expect(outcome).toEqual({ ok: true, data: { id: ME, username: "Грачья А" }, account: ME });
    expect(seen[0]!.query.get("client_id")).toBe("6287487");
    expect(seen[0]!.query.get("v")).toBe("5.289");
    expect(seen[0]!.form.access_token).toBe("vk1.token-1");
    expect(seen[0]!.init).toMatchObject({ method: "POST", credentials: "include" });
    // Из localStorage прочитан только ключ веб-токена.
    expect(vi.mocked(localStorage.getItem).mock.calls).toEqual([[TOKEN_KEY]]);
  });

  it("голая строка токена в localStorage тоже подходит", async () => {
    vi.stubGlobal("localStorage", {
      length: 1,
      key: () => TOKEN_KEY,
      getItem: () => "vk1.raw",
    });
    const seen = stubFetch({ "users.get": { response: [{ id: 100500 }] } });

    await call("whoami", {}, { account: null, verify: "whoami" });

    expect(seen[0]!.form.access_token).toBe("vk1.raw");
  });

  it("токена нет — logged_out без запроса", async () => {
    signIn(null);
    const seen = stubFetch({});

    expect(await call("search", { q: "x", count: 10 })).toMatchObject({
      ok: false,
      code: "logged_out",
    });
    expect(seen).toEqual([]);
  });

  it("поиск: проекция трека без ссылки на аудио и служебных полей", async () => {
    const seen = stubFetch({ "audio.search": { response: { count: 1, items: [RAW_TRACK] } } });

    const outcome = await call("search", { q: "Don Toliver Call Back", count: 20 });

    expect(outcome).toEqual({ ok: true, data: { tracks: [TRACK] } });
    expect(seen[0]!.form).toMatchObject({ q: "Don Toliver Call Back", count: "20" });
    expect(JSON.stringify(outcome)).not.toContain("vkuseraudio");
  });

  it("playlist_page: шапка, весь список id, детали среза по порядку", async () => {
    const ids = ["100500_901_k1", "100500_902_k2", "100500_903_k3"];
    const seen = stubFetch({
      "audio.getPlaylistById": { response: PLAYLIST },
      "audio.getIdsBySource": {
        response: { audios: ids.map((id) => ({ audio_id: id, track_code: "t" })) },
      },
      "audio.getById": (s) => ({
        response: s.form
          .audios!.split(",")
          .reverse()
          .map((id) => {
            const [owner, audio] = id.split("_");
            return { ...RAW_TRACK, owner_id: Number(owner), id: Number(audio), title: `T${audio}` };
          }),
      }),
    });

    const outcome = await call("playlist_page", {
      owner_id: 100500,
      playlist_id: 158,
      access_key: "34350a7d",
      offset: 0,
      limit: 2,
    });

    expect(outcome.ok).toBe(true);
    const data = (outcome as { data: Record<string, any> }).data;
    expect(data.playlist).toEqual({
      owner_id: 100500,
      id: 158,
      access_key: "34350a7d",
      title: "sp-spike",
      description: "Описание",
      count: 3,
      type: "ugc",
      editable: true,
    });
    expect(data.total).toBe(3);
    expect(data.next_offset).toBe(2);
    expect(data.tracks.map((t: { title: string }) => t.title)).toEqual(["T901", "T902"]);
    expect(seen.find((s) => s.method === "audio.getIdsBySource")!.form).toMatchObject({
      source: "playlist",
      entity_id: "100500_158_34350a7d",
    });
    expect(seen.find((s) => s.method === "audio.getById")!.form.audios).toBe(
      "100500_901_k1,100500_902_k2",
    );
  });

  it("пустой плейлист (только что создан): без getIdsBySource и getById", async () => {
    const seen = stubFetch({
      "audio.getPlaylistById": {
        response: { playlist: { ...PLAYLIST.playlist, count: 0, permissions: {} } },
      },
    });

    const outcome = await call("playlist_page", {
      owner_id: 100500,
      playlist_id: 158,
      access_key: "34350a7d",
      offset: 0,
      limit: 2,
    });

    expect(outcome).toMatchObject({ ok: true, data: { total: 0, tracks: [] } });
    expect(seen.map((s) => s.method)).toEqual(["audio.getPlaylistById"]);
  });

  it("library_page: audio.get по id аккаунта задачи, offset/count", async () => {
    const seen = stubFetch({
      "audio.get": { response: { count: 3, items: [RAW_TRACK, RAW_TRACK] } },
    });

    const outcome = await call("library_page", { offset: 0, limit: 2 });

    expect(outcome).toMatchObject({ ok: true, data: { total: 3, next_offset: 2 } });
    expect(seen[0]!.form).toMatchObject({ owner_id: ME, offset: "0", count: "2" });
  });
});

describe("страница VK: запись", () => {
  it("create_playlist: скрытый из поиска, на свой аккаунт, без запроса профиля", async () => {
    const seen = stubFetch({
      "audio.createPlaylist": { response: { ...PLAYLIST.playlist, id: 159, access_key: "ffee" } },
    });

    const outcome = await call("create_playlist", { title: "Перенос", description: "" });

    expect(outcome).toEqual({ ok: true, data: { owner_id: 100500, id: 159, access_key: "ffee" } });
    expect(seen.map((s) => s.method)).toEqual(["audio.createPlaylist"]); // сверка по токену
    expect(seen[0]!.form).toMatchObject({ owner_id: ME, title: "Перенос", no_discover: "1" });
  });

  it("set_playlist_tracks: editPlaylist всем списком с текущими названием и описанием", async () => {
    const seen = stubFetch({
      "audio.getPlaylistById": { response: PLAYLIST },
      "audio.editPlaylist": { response: 1 },
    });

    const outcome = await call("set_playlist_tracks", {
      playlist_id: 158,
      audio_ids: ["100500_901", "-2001_5"],
    });

    expect(outcome).toEqual({ ok: true, data: {} });
    expect(seen[1]!.form).toMatchObject({
      owner_id: ME,
      playlist_id: "158",
      title: "sp-spike",
      description: "Описание",
      no_discover: "1",
      audio_ids: "100500_901,-2001_5",
    });
  });

  it("set_playlist_tracks: getPlaylistById без обёртки {playlist} (без extra_fields)", async () => {
    const seen = stubFetch({
      "audio.getPlaylistById": { response: PLAYLIST.playlist },
      "audio.editPlaylist": { response: 1 },
    });

    const outcome = await call("set_playlist_tracks", { playlist_id: 158, audio_ids: ["-2001_5"] });

    expect(outcome).toEqual({ ok: true, data: {} });
    expect(seen[1]!.form).toMatchObject({ title: "sp-spike", audio_ids: "-2001_5" });
  });

  it("add_to_library: audio.add → новая копия; ошибки в ответе — bad_request", async () => {
    stubFetch({
      "audio.add": {
        response: {
          items_count: 1,
          errors_count: 0,
          items: [{ new_audio_id: 915, new_owner_id: 100500 }],
          errors: [],
        },
      },
    });
    expect(
      await call("add_to_library", { owner_id: -2001, audio_id: 5, access_key: "ab" }),
    ).toEqual({ ok: true, data: { owner_id: 100500, id: 915 } });

    stubFetch({ "audio.add": { response: { items_count: 0, items: [], errors: [{ code: 1 }] } } });
    expect(
      await call("add_to_library", { owner_id: -2001, audio_id: 5, access_key: null }),
    ).toMatchObject({ ok: false, code: "bad_request" });
  });
});

describe("страница VK: ошибки и капча", () => {
  it.each([
    [{ error_code: 14, error_msg: "Captcha needed", captcha_sid: "1" }, "captcha", undefined],
    [{ error_code: 17, error_msg: "Validation required", redirect_uri: "x" }, "captcha", undefined],
    [
      { error_code: 5, error_msg: "User authorization failed: access_token has expired." },
      "token_expired",
      undefined,
    ],
    [{ error_code: 5, error_msg: "User authorization failed" }, "logged_out", undefined],
    [{ error_code: 6, error_msg: "Too many requests per second" }, "rate_limited", 2],
    [{ error_code: 9, error_msg: "Flood control" }, "rate_limited", 300],
    [{ error_code: 15, error_msg: "Access denied" }, "not_writable", undefined],
    [{ error_code: 104, error_msg: "Not found" }, "not_found", undefined],
    [{ error_code: 100, error_msg: "One of the parameters" }, "bad_request", undefined],
    [{ error_code: 10, error_msg: "Internal" }, "unavailable", undefined],
  ])("%o → %s", async (error, code, retryAfter) => {
    stubFetch({ "audio.search": { error } });

    const outcome = await call("search", { q: "x", count: 10 });

    expect(outcome).toMatchObject({ ok: false, code });
    expect((outcome as { retry_after?: number }).retry_after).toBe(retryAfter);
  });

  it("HTML вместо JSON — капча (страница проверки)", async () => {
    stubFetch({ "audio.search": { text: "<!DOCTYPE html><html>проверка</html>" } });
    expect(await call("search", { q: "x", count: 10 })).toMatchObject({ code: "captcha" });
  });

  it("ответ в ошибке не несёт текст VK наружу кроме технической причины", async () => {
    stubFetch({ "audio.search": { error: { error_code: 10, error_msg: "secret name Ivan" } } });
    const outcome = await call("search", { q: "x", count: 10 });
    expect(JSON.stringify(outcome)).not.toContain("Ivan");
  });
});

describe("страница VK: сверка аккаунта", () => {
  it("в записи токена другой пользователь — users.get и session_mismatch с фактическим id", async () => {
    signIn("vk1.t", 200600);
    stubFetch({ "users.get": { response: [{ id: 200600 }] } });

    expect(await call("search", { q: "x", count: 10 })).toEqual({
      ok: false,
      code: "session_mismatch",
      account: "200600",
    });
  });

  it("в записи токена нет id — сверка запросом users.get, в ответе account", async () => {
    signIn("vk1.t", null);
    const seen = stubFetch({
      "users.get": { response: [{ id: 100500 }] },
      "audio.search": { response: { items: [] } },
    });

    expect(await call("search", { q: "x", count: 10 })).toEqual({
      ok: true,
      data: { tracks: [] },
      account: ME,
    });
    expect(seen.map((s) => s.method)).toEqual(["users.get", "audio.search"]);
  });
});

describe("правило 11h: токен VK не покидает страницу", () => {
  const source = vkPage.toString();

  it.each(["sessionStorage", "document.cookie", "Authorization", ".url"])(
    "в функции страницы нет %s",
    (needle) => {
      expect(source).not.toContain(needle);
    },
  );

  it("функция самодостаточна: выполняется в пустом контексте без хелперов бандлера", async () => {
    const context = vm.createContext({
      localStorage: {
        length: 1,
        key: () => TOKEN_KEY,
        getItem: () => JSON.stringify({ access_token: "vk1.x", user_id: 100500 }),
      },
      fetch: async () => new Response(JSON.stringify({ response: [{ id: 100500 }] })),
      URL,
      URLSearchParams,
      AbortController,
      Response,
      setTimeout,
      clearTimeout,
      JSON,
      call: { op: "whoami", args: {}, account: null, verify: "whoami" },
    });

    const outcome = await vm.runInContext(`(${source})(call)`, context);

    expect(outcome).toMatchObject({ ok: true, account: ME });
  });
});

// ------------------------------------------------------------------ роутер

function task(op: string, args: Record<string, unknown>, extra: Partial<TaskMessage> = {}) {
  return {
    type: "task" as const,
    task_id: `t-${Math.random()}`,
    op: `vk.${op}`,
    args,
    deadline: Date.now() / 1000 + 600,
    idempotency_key: null,
    account: ME,
    ...extra,
  };
}

function router(overrides: Partial<RouterDeps> = {}) {
  let clock = Date.now();
  const sent: ResultMessage[] = [];
  const states: Array<[string, string, string | null]> = [];
  const deps: RouterDeps = {
    registry: buildRegistry(),
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
    random: () => 0.5,
    ...overrides,
  };
  return { router: new TaskRouter(deps), sent, states, time: () => clock };
}

describe("роутер VK", () => {
  it("истёкший токен: перезагрузка вкладки, пауза и один повтор со свежим токеном", async () => {
    const reloads: string[] = [];
    const seen = stubFetch({
      "audio.search": (s) =>
        s.form.access_token === "vk1.fresh"
          ? { response: { items: [] } }
          : { error: { error_code: 5, error_msg: "access_token has expired." } },
    });
    const { router: r, sent } = router({
      reloadTarget: async (target) => {
        reloads.push(target.key);
        (localStorage as unknown as { setToken(v: string): void }).setToken("vk1.fresh");
      },
    });

    await r.handle(task("search", { q: "x", count: 10 }));

    expect(reloads).toEqual(["vk"]);
    expect(sent[0]).toMatchObject({ ok: true, data: { tracks: [] } });
    expect(seen.filter((s) => s.method === "audio.search")).toHaveLength(2);
  });

  it("истёк и после перезагрузки — выход из аккаунта (logged_out), в состоянии площадки тоже", async () => {
    stubFetch({
      "audio.search": { error: { error_code: 5, error_msg: "access_token has expired" } },
    });
    const { router: r, sent, states } = router({ reloadTarget: async () => undefined });

    await r.handle(task("search", { q: "x", count: 10 }));

    expect(sent[0]).toMatchObject({ ok: false, error: { code: "logged_out" } });
    expect(states).toEqual([["vk", "logged_out", null]]);
  });

  it("«Моя музыка» — не чаще раза в 2–3 с, прочее — раз в секунду", async () => {
    const { router: r, time } = router();
    const at: Record<string, number[]> = { add: [], search: [] };
    stubFetch({
      "audio.add": () => {
        at.add!.push(time());
        return { response: { items: [{ new_audio_id: 1, new_owner_id: 100500 }], errors: [] } };
      },
      "audio.search": () => {
        at.search!.push(time());
        return { response: { items: [] } };
      },
    });

    for (let i = 0; i < 3; i++) {
      await r.handle(
        task("add_to_library", { owner_id: -2001, audio_id: i + 1, access_key: null }),
      );
    }
    for (let i = 0; i < 3; i++) await r.handle(task("search", { q: `q${i}`, count: 5 }));

    for (let i = 1; i < 3; i++) {
      const gap = at.add![i]! - at.add![i - 1]!;
      expect(gap).toBeGreaterThanOrEqual(2000);
      expect(gap).toBeLessThanOrEqual(3000);
      expect(at.search![i]! - at.search![i - 1]!).toBeGreaterThanOrEqual(1000);
    }
  });

  it("капча: captcha в ответе и в состоянии площадки", async () => {
    stubFetch({ "audio.search": { error: { error_code: 14, captcha_sid: "1" } } });
    const { router: r, sent, states } = router();

    await r.handle(task("search", { q: "x", count: 10 }));

    expect(sent[0]).toMatchObject({ ok: false, error: { code: "captcha" } });
    expect(states).toEqual([["vk", "captcha", null]]);
  });

  it("аргументы не по схеме — bad_args, в страницу не идём", async () => {
    const seen = stubFetch({});
    const { router: r, sent } = router();

    await r.handle(task("tracks", { ids: ["1_2;drop"] }));
    await r.handle(task("set_playlist_tracks", { playlist_id: 1, audio_ids: ["1_2_key"] }));
    await r.handle(task("library_page", { offset: 0, limit: 5000 }));

    expect(seen).toEqual([]);
    expect(sent.map((m) => m.error?.code)).toEqual(["bad_args", "bad_args", "bad_args"]);
  });

  it("probe whoami: кто вошёл (для подключения площадки)", async () => {
    stubFetch({ "users.get": { response: [{ id: 100500, first_name: "Me" }] } });
    const { router: r } = router();

    expect(await r.probe("vk.whoami")).toEqual({ ok: true, data: { id: ME, username: "Me" } });
  });
});

// Проверка типов: функция страницы возвращает PageOutcome.
const _typed: (call: PageCall) => Promise<PageOutcome> = vkPage;
