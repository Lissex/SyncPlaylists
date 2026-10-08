// Яндекс Музыка через расширение (этап 4c-4): поиск, чтение и запись — запросами к
// api.music.yandex.ru из MAIN world страницы music.yandex.ru. Авторизация — только cookie
// сессии (`credentials: "include"`). Токен Яндекса расширение не читает вообще — ни из
// cookie, ни из localStorage, ни из ответов — и служебные заголовки сайта x-yandex-music-*
// не подставляет (ARCHITECTURE.md 11h). Антибот Яндекса — на сервере (капча по cookie):
// её видит и проходит человек на сайте, расширение только сообщает «captcha».
//
// Вся работа в странице — одна самодостаточная функция yandexPage: executeScript
// сериализует её через toString(), поэтому никаких импортов и внешних функций.
//
// Ответ Яндекса не уходит как есть: из него вырезаются только поля, которые нужны серверу
// (проекция; сервер проверяет ту же форму — integrations/platforms/yandex/extension_wire.py).
// Имена полей — как у API (camelCase).

import { z } from "zod";

import { platformInfo, platformTarget } from "../platforms";
import type { OperationDef, PageCall, PageOutcome } from "./registry";

// ------------------------------------------------------------------ в странице

export async function yandexPage(call: PageCall): Promise<PageOutcome> {
  const API = "https://api.music.yandex.ru";
  const op = call.op;
  const args = call.args as Record<string, any>;
  const write = ["create_playlist", "set_description", "add_tracks", "like_many"].includes(op);

  // Логин из cookie yandex_login — для сверки аккаунта без запроса. Это не токен и не
  // cookie сессии (та — HttpOnly, страница её и не видит); наружу логин не уходит.
  const cookieLogin = ((): string | null => {
    const part = document.cookie
      .split(";")
      .map((item) => item.trim())
      .find((item) => item.startsWith("yandex_login="));
    const value = part ? decodeURIComponent(part.slice("yandex_login=".length)) : "";
    return value || null;
  })();

  class PageFailure extends Error {
    constructor(
      readonly code: string,
      readonly retryAfter?: number,
      readonly detail?: string,
    ) {
      super(code);
    }
  }
  const describe = (error: unknown): string =>
    (error instanceof Error ? `${error.name}: ${error.message}` : String(error)).slice(0, 150);

  const object = (value: unknown): Record<string, any> | undefined =>
    value && typeof value === "object" && !Array.isArray(value)
      ? (value as Record<string, any>)
      : undefined;
  const list = (value: unknown): unknown[] => (Array.isArray(value) ? value : []);
  const text = (value: unknown, max = 500): string | undefined =>
    typeof value === "string" ? value.slice(0, max) : undefined;
  const count = (value: unknown): number | undefined =>
    typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : undefined;
  // id трека/альбома: у API бывает и числом, и строкой — на провод всегда строкой.
  const idText = (value: unknown): string | undefined => {
    const raw = typeof value === "number" && Number.isSafeInteger(value) ? String(value) : value;
    return typeof raw === "string" && /^[A-Za-z0-9-]{1,64}$/.test(raw) ? raw : undefined;
  };
  const uidText = (value: unknown): string | undefined => {
    const raw = idText(value);
    return raw !== undefined && /^\d{1,20}$/.test(raw) ? raw : undefined;
  };
  const prune = <T extends Record<string, unknown>>(value: T): T =>
    Object.fromEntries(Object.entries(value).filter(([, v]) => v !== undefined)) as T;
  // executeScript может превратить null в аргументах в undefined — считаем их одним.
  const given = (value: unknown): boolean => value !== null && value !== undefined;

  function errorName(body: string): string {
    try {
      const error = object(JSON.parse(body))?.error;
      return String(object(error)?.name ?? object(error)?.message ?? error ?? "");
    } catch {
      return "";
    }
  }

  async function request(
    method: string,
    path: string,
    query: Record<string, unknown> = {},
    form?: Record<string, string>,
  ): Promise<any> {
    const url = new URL(API + path);
    for (const [key, value] of Object.entries(query)) {
      if (given(value)) url.searchParams.set(key, String(value));
    }
    const headers: Record<string, string> = { Accept: "application/json" };
    let body: string | undefined;
    if (form) {
      headers["Content-Type"] = "application/x-www-form-urlencoded";
      body = new URLSearchParams(form).toString();
    }
    let response: Response;
    const abort = new AbortController();
    const timer = setTimeout(() => abort.abort(), 20_000);
    try {
      response = await fetch(url.toString(), {
        method,
        headers,
        body,
        credentials: "include",
        // Антибот отправляет на страницу капчи редиректом — его не проходим, а сообщаем.
        redirect: "manual",
        signal: abort.signal,
      });
    } catch (error) {
      throw new PageFailure("unavailable", undefined, `fetch ${path}: ${describe(error)}`);
    } finally {
      clearTimeout(timer);
    }
    if (response.type === "opaqueredirect" || (response.status >= 300 && response.status < 400)) {
      throw new PageFailure("captcha", undefined, `redirect ${path}`);
    }
    const status = response.status;
    const type = response.headers.get("content-type") ?? "";
    const raw = await response.text().catch(() => "");
    // Капча антибота (11g): 429 с x-yandex-captcha и HTML-страницей. Заголовок из-за CORS
    // может быть не виден — тогда узнаём по HTML вместо JSON.
    const html = type.includes("text/html") || /^\s*</.test(raw);
    const captcha =
      response.headers.has("x-yandex-captcha") || /showcaptcha|smartcaptcha/i.test(raw);
    if (response.ok) {
      if (captcha || html) throw new PageFailure("captcha", undefined, `HTML ${path}`);
      let parsed: unknown;
      try {
        parsed = raw ? JSON.parse(raw) : {};
      } catch {
        throw new PageFailure("unavailable", undefined, `не JSON ${path}`);
      }
      const envelope = object(parsed);
      return envelope && "result" in envelope ? envelope.result : parsed;
    }
    if (captcha || (status === 429 && html)) {
      throw new PageFailure("captcha", undefined, `HTTP ${status} ${path}`);
    }
    if (errorName(raw).includes("wrong-revision")) throw new PageFailure("conflict");
    if (status === 401) throw new PageFailure("logged_out");
    // 403: чужой плейлист на запись или закрытый на чтение — сервер решает по контексту.
    if (status === 403) throw new PageFailure("not_writable");
    if (status === 404) throw new PageFailure("not_found");
    if (status === 429) {
      const seconds = Number(response.headers.get("retry-after"));
      throw new PageFailure("rate_limited", Number.isFinite(seconds) && seconds > 0 ? seconds : 60);
    }
    if (status >= 500 || status === 451) {
      throw new PageFailure("unavailable", undefined, `HTTP ${status}`);
    }
    throw new PageFailure(write ? "bad_request" : "not_found", undefined, `HTTP ${status}`);
  }

  function track(value: unknown): Record<string, unknown> | undefined {
    const t = object(value);
    const id = idText(t?.id);
    if (!t || id === undefined) return undefined;
    return prune({
      id,
      title: text(t.title),
      version: text(t.version),
      available: typeof t.available === "boolean" ? t.available : undefined,
      durationMs: count(t.durationMs),
      coverUri: text(t.coverUri, 2000),
      ogImage: text(t.ogImage, 2000),
      artists: list(t.artists)
        .map(object)
        .filter((a) => a !== undefined)
        .map((a) => prune({ id: idText(a.id), name: text(a.name) }))
        .slice(0, 50),
      albums: list(t.albums)
        .map((a) => idText(object(a)?.id))
        .filter((a) => a !== undefined)
        .map((albumId) => ({ id: albumId }))
        .slice(0, 50),
    });
  }

  function short(value: unknown): Record<string, unknown> | undefined {
    const s = object(value);
    const id = idText(s?.id);
    if (!s || id === undefined) return undefined;
    return prune({ id, albumId: idText(s.albumId), timestamp: text(s.timestamp, 40) });
  }

  // Срез списка записей одной ревизии: offset/limit из аргументов; если список уже не той
  // ревизии, что первая страница, — conflict (сервер прочитает заново).
  function slice(
    shorts: unknown[],
    revision: number,
  ): { tracks: Record<string, unknown>[]; nextOffset?: number } {
    if (given(args.revision) && args.revision !== revision) throw new PageFailure("conflict");
    const offset = Number(args.offset ?? 0);
    const limit = Number(args.limit);
    const all = shorts.map(short).filter((s) => s !== undefined);
    const end = offset + limit;
    return prune({
      tracks: all.slice(offset, end),
      nextOffset: end < all.length ? end : undefined,
    });
  }

  let me: { uid: string; login: string | null; name: string | null } | null = null;
  async function whoami(): Promise<{ uid: string; login: string | null; name: string | null }> {
    if (me === null) {
      // Без входа account/status отвечает 200 с анонимным аккаунтом (без uid).
      const account = object(object(await request("GET", "/account/status"))?.account);
      const uid = uidText(account?.uid);
      if (!account || uid === undefined) throw new PageFailure("logged_out");
      me = {
        uid,
        login: text(account.login, 200) ?? null,
        name: text(account.displayName, 200) ?? text(account.fullName, 200) ?? null,
      };
    }
    return me;
  }

  try {
    // Сверка аккаунта до операции. Дёшево — по cookie yandex_login против логина, который
    // вернула прошлая полная проверка этого аккаунта; иначе — полным account/status.
    let verified: { uid: string; login: string | null } | undefined;
    const expected = typeof call.account === "string" && call.account ? call.account : null;
    const hint =
      typeof call.account_hint === "string" && call.account_hint ? call.account_hint : null;
    if (expected !== null) {
      const cheap =
        call.verify === "cookie" &&
        hint !== null &&
        cookieLogin !== null &&
        cookieLogin.toLowerCase() === hint.toLowerCase();
      if (!cheap) {
        const profile = await whoami();
        verified = profile;
        if (profile.uid !== expected) {
          return { ok: false, code: "session_mismatch", account: profile.uid };
        }
      }
    }
    const done = (data: Record<string, unknown>): PageOutcome => {
      if (verified === undefined) return { ok: true, data };
      return verified.login
        ? { ok: true, data, account: verified.uid, hint: verified.login }
        : { ok: true, data, account: verified.uid };
    };
    const uid = async (): Promise<string> => expected ?? (await whoami()).uid;

    switch (op) {
      case "whoami": {
        const profile = await whoami();
        const outcome: PageOutcome = {
          ok: true,
          data: prune({
            id: profile.uid,
            username: profile.name ?? profile.login ?? undefined,
            login: profile.login ?? undefined,
          }),
          account: profile.uid,
        };
        if (profile.login) outcome.hint = profile.login;
        return outcome;
      }
      case "search": {
        const body = object(
          await request("GET", "/search", { text: args.text, type: "track", page: 0 }),
        );
        const results = list(object(body?.tracks)?.results);
        return done({
          tracks: results
            .map(track)
            .filter((t) => t !== undefined)
            .slice(0, 50),
        });
      }
      case "tracks": {
        const body = await request(
          "POST",
          "/tracks",
          {},
          {
            "track-ids": (args.ids as string[]).join(","),
            "with-positions": "True",
          },
        );
        return done({
          tracks: list(body)
            .map(track)
            .filter((t) => t !== undefined),
        });
      }
      case "playlist_page": {
        const path = given(args.uuid)
          ? `/playlist/${encodeURIComponent(args.uuid)}`
          : `/users/${encodeURIComponent(args.owner)}/playlists/${Number(args.kind)}`;
        const body = object(await request("GET", path));
        const kind = count(body?.kind);
        const revision = count(body?.revision) ?? 0;
        if (!body || kind === undefined) return { ok: false, code: "not_found" };
        const owner = object(body.owner);
        const shorts = list(body.tracks);
        return done(
          prune({
            kind,
            uid: uidText(body.uid),
            owner: owner ? prune({ uid: uidText(owner.uid), login: text(owner.login) }) : undefined,
            title: text(body.title),
            description: text(body.description, 5000),
            revision,
            trackCount: count(body.trackCount) ?? shorts.length,
            ...slice(shorts, revision),
          }),
        );
      }
      case "library_ids_page": {
        const body = object(await request("GET", `/users/${await uid()}/likes/tracks`));
        const library = object(body?.library);
        const revision = count(library?.revision) ?? 0;
        return done({ revision, ...slice(list(library?.tracks), revision) });
      }
      case "create_playlist": {
        const body = object(
          await request(
            "POST",
            `/users/${await uid()}/playlists/create`,
            {},
            {
              title: String(args.title),
              visibility: "private",
            },
          ),
        );
        const kind = count(body?.kind);
        if (kind === undefined) return { ok: false, code: "unavailable" };
        return done(prune({ kind, revision: count(body?.revision) }));
      }
      case "set_description": {
        await request(
          "POST",
          `/users/${await uid()}/playlists/${Number(args.kind)}/description`,
          {},
          {
            value: String(args.description),
          },
        );
        return done({});
      }
      case "add_tracks": {
        const tracks = (args.tracks as Array<{ id: string; album_id?: string | null }>).map((t) =>
          given(t.album_id) ? { id: t.id, albumId: t.album_id } : { id: t.id },
        );
        const diff = [{ op: "insert", at: Number(args.at), tracks }];
        const body = object(
          await request(
            "POST",
            `/users/${await uid()}/playlists/${Number(args.kind)}/change-relative`,
            {},
            { diff: JSON.stringify(diff), revision: String(args.revision) },
          ),
        );
        const revision = count(body?.revision);
        if (revision === undefined) return { ok: false, code: "unavailable" };
        return done(prune({ revision, trackCount: count(body?.trackCount) }));
      }
      case "like_many": {
        await request(
          "POST",
          `/users/${await uid()}/likes/tracks/add-multiple`,
          {},
          {
            "track-ids": (args.ids as string[]).join(","),
          },
        );
        return done({});
      }
      default:
        return { ok: false, code: "unavailable" };
    }
  } catch (error) {
    if (error instanceof PageFailure) {
      const failure: { ok: false; code: any; retry_after?: number; detail?: string } = {
        ok: false,
        code: error.code,
      };
      if (error.retryAfter !== undefined) failure.retry_after = error.retryAfter;
      if (error.detail !== undefined) failure.detail = error.detail;
      return failure as PageOutcome;
    }
    return { ok: false, code: "unavailable", detail: describe(error) };
  }
}

// ------------------------------------------------------------------ схемы (background)

const MAX_PAGE = 5000;
const MAX_TRACKS = 100;

const id = z.string().regex(/^[A-Za-z0-9-]{1,64}$/);
const uid = z.string().regex(/^\d{1,20}$/);
// Трек в запросе: "<id>" или "<id>:<альбом>" — как track-ids у Яндекса.
const trackRef = z.string().regex(/^[A-Za-z0-9-]{1,64}(:\d{1,20})?$/);
const count = z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER);
const shortText = z.string().max(500);
const url = z.string().max(2000);

const track = z.strictObject({
  id,
  title: shortText.optional(),
  version: shortText.optional(),
  available: z.boolean().optional(),
  durationMs: count.optional(),
  coverUri: url.optional(),
  ogImage: url.optional(),
  artists: z.array(z.strictObject({ id: id.optional(), name: shortText.optional() })).max(50),
  albums: z.array(z.strictObject({ id })).max(50),
});

const short = z.strictObject({
  id,
  albumId: id.optional(),
  timestamp: z.string().max(40).optional(),
});

const page = {
  offset: count,
  limit: z.number().int().min(1).max(MAX_PAGE),
  revision: count.nullable(),
};

const empty = z.strictObject({});

interface YmOperation {
  name: string;
  write: boolean;
  args: z.ZodType<Record<string, unknown>>;
  result: z.ZodType<Record<string, unknown>>;
}

const OPERATIONS: YmOperation[] = [
  {
    name: "whoami",
    write: false,
    args: empty,
    result: z.strictObject({
      id: uid,
      username: shortText.optional(),
      login: shortText.optional(),
    }),
  },
  {
    name: "search",
    write: false,
    args: z.strictObject({ text: z.string().min(1).max(300) }),
    result: z.strictObject({ tracks: z.array(track).max(MAX_TRACKS) }),
  },
  {
    name: "tracks",
    write: false,
    args: z.strictObject({ ids: z.array(trackRef).min(1).max(MAX_TRACKS) }),
    result: z.strictObject({ tracks: z.array(track).max(MAX_TRACKS) }),
  },
  {
    name: "playlist_page",
    write: false,
    // Плейлист — либо владелец (логин или uid) + kind, либо uuid из новых ссылок.
    args: z
      .strictObject({
        owner: z
          .string()
          .regex(/^[A-Za-z0-9._-]{1,100}$/)
          .nullable(),
        kind: count.nullable(),
        uuid: id.nullable(),
        ...page,
      })
      .refine((a) => (a.uuid === null) === (a.owner !== null && a.kind !== null)),
    result: z.strictObject({
      kind: count,
      uid: uid.optional(),
      owner: z.strictObject({ uid: uid.optional(), login: shortText.optional() }).optional(),
      title: shortText.optional(),
      description: z.string().max(5000).optional(),
      revision: count,
      trackCount: count,
      tracks: z.array(short).max(MAX_PAGE),
      nextOffset: count.optional(),
    }),
  },
  {
    name: "library_ids_page",
    write: false,
    args: z.strictObject(page),
    result: z.strictObject({
      revision: count,
      tracks: z.array(short).max(MAX_PAGE),
      nextOffset: count.optional(),
    }),
  },
  {
    name: "create_playlist",
    write: true,
    args: z.strictObject({ title: z.string().min(1).max(500) }),
    result: z.strictObject({ kind: count, revision: count.optional() }),
  },
  {
    name: "set_description",
    write: true,
    args: z.strictObject({ kind: count, description: z.string().max(5000) }),
    result: empty,
  },
  {
    name: "add_tracks",
    write: true,
    args: z.strictObject({
      kind: count,
      revision: count,
      at: count,
      tracks: z
        .array(
          z.strictObject({
            id,
            album_id: z
              .string()
              .regex(/^\d{1,20}$/)
              .nullable(),
          }),
        )
        .min(1)
        .max(MAX_TRACKS),
    }),
    result: z.strictObject({ revision: count, trackCount: count.optional() }),
  },
  {
    name: "like_many",
    write: true,
    args: z.strictObject({ ids: z.array(trackRef).min(1).max(MAX_TRACKS) }),
    result: empty,
  },
];

export function yandexOperations(): OperationDef[] {
  const target = platformTarget(platformInfo("yandex")!);
  return OPERATIONS.map((operation) => ({
    op: `yandex.${operation.name}`,
    target,
    write: operation.write,
    session: true,
    args: operation.args,
    result: operation.result,
    main: yandexPage,
  }));
}
