// SoundCloud через расширение (этап 4c-3): личное и запись — запросами к api-v2 из MAIN
// world страницы soundcloud.com (fetch сайта обёрнут DataDome — антибот проходит браузер,
// ARCHITECTURE.md 11h). Публичное (поиск, публичные сеты) сервер читает сам.
//
// Вся работа в странице — одна самодостаточная функция soundcloudPage: executeScript
// сериализует её через toString(), поэтому никаких импортов и внешних функций, всё
// (fetch, разбор ошибок, проекция ответа) — внутри.
//
// Токен сайта (cookie oauth_token) читается в странице и её не покидает. Ответ
// SoundCloud не уходит как есть: из него вырезаются только поля, которые нужны серверу
// (проекция; сервер проверяет ту же форму — integrations/platforms/soundcloud/extension_wire.py).
// Секрет приватного сета на проводе — `secret` (ключи с «token» в протоколе запрещены).

import { z } from "zod";

import { platformInfo, platformTarget } from "../platforms";
import type { OperationDef, PageCall, PageOutcome } from "./registry";

// ------------------------------------------------------------------ в странице

export async function soundcloudPage(call: PageCall): Promise<PageOutcome> {
  const API = "https://api-v2.soundcloud.com";
  const op = call.op;
  const args = call.args as Record<string, unknown>;
  const write = ["like", "create_playlist", "set_playlist_tracks"].includes(op);

  const raw = document.cookie
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith("oauth_token="));
  const token = raw ? decodeURIComponent(raw.slice("oauth_token=".length)) : "";
  if (!token) return { ok: false, code: "logged_out" };

  // id аккаунта из самого токена, без запроса: старый формат "2-<n>-<id>-<...>" или JWT
  // (claim sub вида "soundcloud:users:<id>"). null — формат не узнали.
  const cookieAccount = ((): string | null => {
    const legacy = /^\d+-\d+-(\d+)-/.exec(token);
    if (legacy) return legacy[1]!;
    const parts = token.split(".");
    if (parts.length !== 3) return null;
    try {
      const json = atob(parts[1]!.replace(/-/g, "+").replace(/_/g, "/"));
      const claims = JSON.parse(json) as Record<string, unknown>;
      const subject = String(claims.sub ?? claims.user_id ?? "")
        .split(":")
        .pop();
      return subject && /^\d+$/.test(subject) ? subject : null;
    } catch {
      return null;
    }
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
  // Короткая техническая причина (тип и текст исключения, HTTP-код) — только для окна
  // расширения; на сервер уходит лишь код.
  const describe = (error: unknown): string =>
    (error instanceof Error ? `${error.name}: ${error.message}` : String(error)).slice(0, 150);

  async function request(
    method: string,
    path: string,
    query: Record<string, unknown> = {},
    body?: unknown,
    cursor?: unknown,
  ): Promise<any> {
    const url = new URL(API + path);
    if (typeof cursor === "string" && cursor) {
      new URLSearchParams(cursor).forEach((value, key) => url.searchParams.set(key, value));
    }
    for (const [key, value] of Object.entries(query)) {
      if (value !== null && value !== undefined) url.searchParams.set(key, String(value));
    }
    const headers: Record<string, string> = {
      Authorization: `OAuth ${token}`,
      Accept: "application/json; charset=utf-8",
    };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    let response: Response;
    // Запрос идёт через fetch сайта (его оборачивает антибот) — без таймаута он может
    // повиснуть, и задача не закончится никогда.
    const abort = new AbortController();
    const timer = setTimeout(() => abort.abort(), 20_000);
    try {
      response = await fetch(url.toString(), {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: abort.signal,
      });
    } catch (error) {
      throw new PageFailure("unavailable", undefined, `fetch ${path}: ${describe(error)}`);
    } finally {
      clearTimeout(timer);
    }
    if (response.ok) {
      const text = await response.text();
      return text ? JSON.parse(text) : null;
    }
    const status = response.status;
    if (status === 401) throw new PageFailure("logged_out");
    if (status === 403) {
      // DataDome: 403 с заголовком x-datadome или страницей капчи — решает человек.
      const text = await response.text().catch(() => "");
      if (response.headers.has("x-datadome") || text.includes("captcha-delivery.com")) {
        throw new PageFailure("captcha");
      }
      throw new PageFailure("not_writable");
    }
    if (status === 404) throw new PageFailure("not_found");
    if (status === 429) {
      const seconds = Number(response.headers.get("retry-after"));
      throw new PageFailure("rate_limited", Number.isFinite(seconds) && seconds > 0 ? seconds : 60);
    }
    if (status >= 500) throw new PageFailure("unavailable", undefined, `HTTP ${status}`);
    throw new PageFailure(write ? "not_writable" : "not_found");
  }

  const text = (value: unknown, max = 500): string | undefined =>
    typeof value === "string" ? value.slice(0, max) : undefined;
  const id = (value: unknown): number | undefined =>
    typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : undefined;
  const prune = <T extends Record<string, unknown>>(value: T): T =>
    Object.fromEntries(Object.entries(value).filter(([, v]) => v !== undefined)) as T;
  const object = (value: unknown): Record<string, any> | undefined =>
    value && typeof value === "object" && !Array.isArray(value)
      ? (value as Record<string, any>)
      : undefined;

  function user(value: unknown): Record<string, unknown> | undefined {
    const u = object(value);
    if (!u) return undefined;
    return prune({
      id: id(u.id),
      username: text(u.username),
      permalink: text(u.permalink),
      verified: typeof u.verified === "boolean" ? u.verified : undefined,
      avatar_url: text(u.avatar_url, 2000),
    });
  }

  function track(value: unknown): Record<string, unknown> | undefined {
    const t = object(value);
    if (!t || id(t.id) === undefined) return undefined;
    const meta = object(t.publisher_metadata);
    return prune({
      id: id(t.id),
      kind: t.kind === "track" ? "track" : undefined,
      title: text(t.title),
      duration: id(t.duration),
      full_duration: id(t.full_duration),
      policy: text(t.policy, 20),
      artwork_url: text(t.artwork_url, 2000),
      user: user(t.user),
      publisher_metadata: meta
        ? prune({ artist: text(meta.artist), isrc: text(meta.isrc, 40) })
        : undefined,
    });
  }

  const collection = (body: unknown): unknown[] => {
    const b = object(body);
    return Array.isArray(b?.collection) ? b.collection : Array.isArray(body) ? body : [];
  };

  // Курсор следующей страницы — query из next_href без client_id/limit. Сам URL наружу
  // не отдаём, и курсор применяется только к тому же пути.
  function nextCursor(body: unknown, path: string): string | null {
    const href = object(body)?.next_href;
    if (typeof href !== "string") return null;
    const next = new URL(href);
    if (next.pathname !== new URL(API + path).pathname) {
      throw new PageFailure("unavailable", undefined, "next_href на другой путь");
    }
    for (const key of ["client_id", "limit", "linked_partitioning"]) next.searchParams.delete(key);
    const cursor = next.searchParams.toString();
    return cursor ? cursor.slice(0, 1000) : null;
  }

  let me: Record<string, any> | null = null;
  async function whoami(): Promise<Record<string, any>> {
    if (me === null) {
      me = object(await request("GET", "/me")) ?? {};
      if (id(me.id) === undefined) throw new PageFailure("logged_out");
    }
    return me;
  }
  async function ownId(): Promise<string> {
    return cookieAccount ?? String((await whoami()).id);
  }

  try {
    // Сверка аккаунта до операции: тот ли пользователь вошёл на сайте, для которого
    // задача. Запись — по cookie (без запроса), чтение — полным /me (роутер решает, когда).
    let verified: string | undefined;
    // Аргументы приходят через executeScript: null там может превратиться в undefined —
    // сверяем, только если аккаунт действительно передан строкой.
    const expected = typeof call.account === "string" && call.account ? call.account : null;
    if (expected !== null) {
      let actual: string;
      if (call.verify === "whoami" || cookieAccount === null) {
        actual = String((await whoami()).id);
        verified = actual;
      } else {
        actual = cookieAccount;
      }
      if (actual !== expected) return { ok: false, code: "session_mismatch", account: actual };
    }
    const done = (data: Record<string, unknown>): PageOutcome =>
      verified === undefined ? { ok: true, data } : { ok: true, data, account: verified };

    switch (op) {
      case "whoami": {
        const profile = await whoami();
        return {
          ok: true,
          data: prune({
            id: id(profile.id),
            username: text(profile.username),
            permalink: text(profile.permalink),
            likes_count: id(profile.likes_count),
          }),
          account: String(profile.id),
        };
      }
      case "liked_tracks_page": {
        const path = `/users/${await ownId()}/track_likes`;
        const body = await request(
          "GET",
          path,
          { limit: args.limit, linked_partitioning: 1 },
          undefined,
          args.cursor,
        );
        const tracks = collection(body)
          .map((like) => track(object(like)?.track))
          .filter((t) => t !== undefined);
        return done({ tracks, next_cursor: nextCursor(body, path) });
      }
      case "liked_track_ids_page": {
        const path = "/me/track_likes/ids";
        const body = await request(
          "GET",
          path,
          { limit: 5000, linked_partitioning: 1 },
          undefined,
          args.cursor,
        );
        const ids = collection(body).filter((i) => id(i) !== undefined);
        return done({ ids, next_cursor: nextCursor(body, path) });
      }
      case "playlist": {
        const body = object(
          await request("GET", `/playlists/${args.playlist_id}`, { secret_token: args.secret }),
        );
        if (!body || id(body.id) === undefined) return { ok: false, code: "not_found" };
        const tracks = (Array.isArray(body.tracks) ? body.tracks : [])
          .map(track)
          .filter((t: unknown) => t !== undefined);
        return done(
          prune({
            id: id(body.id),
            kind: body.kind === "playlist" ? "playlist" : undefined,
            title: text(body.title),
            description: text(body.description, 5000),
            user_id: id(body.user_id) ?? id(object(body.user)?.id),
            secret: text(body.secret_token, 100),
            track_count: id(body.track_count),
            tracks,
          }),
        );
      }
      case "tracks": {
        const body = await request("GET", "/tracks", {
          ids: (args.ids as number[]).join(","),
          playlistId: args.playlist_id,
          playlistSecretToken: args.secret,
        });
        return done({
          tracks: collection(body)
            .map(track)
            .filter((t) => t !== undefined),
        });
      }
      case "like": {
        await request("PUT", `/users/${await ownId()}/track_likes/${args.track_id}`);
        return done({});
      }
      case "create_playlist": {
        const playlist: Record<string, unknown> = {
          title: args.title,
          sharing: "private",
          tracks: [],
        };
        if (args.description) playlist.description = args.description;
        const body = object(await request("POST", "/playlists", {}, { playlist }));
        if (!body || id(body.id) === undefined) return { ok: false, code: "unavailable" };
        return done(prune({ id: id(body.id), secret: text(body.secret_token, 100) }));
      }
      case "set_playlist_tracks": {
        await request(
          "PUT",
          `/playlists/${args.playlist_id}`,
          { secret_token: args.secret },
          { playlist: { tracks: args.track_ids } },
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

const trackId = z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER);
const cursor = z
  .string()
  .max(1000)
  .regex(/^[A-Za-z0-9_\-.,:%=&+~]*$/)
  .nullable();
const secret = z.string().regex(/^s-[A-Za-z0-9]{1,64}$/);
const shortText = z.string().max(500);
const url = z.string().max(2000);

const user = z.strictObject({
  id: trackId.optional(),
  username: shortText.optional(),
  permalink: shortText.optional(),
  verified: z.boolean().optional(),
  avatar_url: url.optional(),
});

const track = z.strictObject({
  id: trackId,
  kind: z.literal("track").optional(),
  title: shortText.optional(),
  duration: trackId.optional(),
  full_duration: trackId.optional(),
  policy: z.string().max(20).optional(),
  artwork_url: url.optional(),
  user: user.optional(),
  publisher_metadata: z
    .strictObject({ artist: shortText.optional(), isrc: z.string().max(40).optional() })
    .optional(),
});

const empty = z.strictObject({});

interface ScOperation {
  name: string;
  write: boolean;
  args: z.ZodType<Record<string, unknown>>;
  result: z.ZodType<Record<string, unknown>>;
}

const OPERATIONS: ScOperation[] = [
  {
    name: "whoami",
    write: false,
    args: empty,
    result: z.strictObject({
      id: trackId,
      username: shortText.optional(),
      permalink: shortText.optional(),
      likes_count: trackId.optional(),
    }),
  },
  {
    name: "liked_tracks_page",
    write: false,
    args: z.strictObject({ cursor, limit: z.number().int().min(1).max(200) }),
    result: z.strictObject({ tracks: z.array(track).max(500), next_cursor: cursor }),
  },
  {
    name: "liked_track_ids_page",
    write: false,
    args: z.strictObject({ cursor }),
    result: z.strictObject({ ids: z.array(trackId).max(5000), next_cursor: cursor }),
  },
  {
    name: "playlist",
    write: false,
    args: z.strictObject({ playlist_id: trackId, secret }),
    result: z.strictObject({
      id: trackId,
      kind: z.literal("playlist").optional(),
      title: shortText.optional(),
      description: z.string().max(5000).optional(),
      user_id: trackId.optional(),
      secret: z.string().max(100).optional(),
      track_count: trackId.optional(),
      tracks: z.array(track).max(1000),
    }),
  },
  {
    name: "tracks",
    write: false,
    args: z.strictObject({
      ids: z.array(trackId).min(1).max(50),
      playlist_id: trackId,
      secret,
    }),
    result: z.strictObject({ tracks: z.array(track).max(100) }),
  },
  {
    name: "like",
    write: true,
    args: z.strictObject({ track_id: trackId }),
    result: empty,
  },
  {
    name: "create_playlist",
    write: true,
    args: z.strictObject({
      title: z.string().min(1).max(500),
      description: z.string().max(5000).nullable(),
    }),
    result: z.strictObject({ id: trackId, secret: z.string().max(100).optional() }),
  },
  {
    name: "set_playlist_tracks",
    write: true,
    args: z.strictObject({
      playlist_id: trackId,
      secret: secret.nullable(),
      track_ids: z.array(trackId).max(500),
    }),
    result: empty,
  },
];

export function soundcloudOperations(): OperationDef[] {
  const target = platformTarget(platformInfo("soundcloud")!);
  return OPERATIONS.map((operation) => ({
    op: `soundcloud.${operation.name}`,
    target,
    write: operation.write,
    session: true,
    args: operation.args,
    result: operation.result,
    main: soundcloudPage,
  }));
}
