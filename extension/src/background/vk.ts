// VK Музыка через расширение (этап 4c-5): поиск, чтение и запись — вызовами
// web.api.vk.ru из MAIN world страницы vk.ru. Серверного пути у VK нет.
//
// Авторизация — веб-токен самой страницы: без него API отвечает error 5 (спайк 4c-5-0).
// Правило 11h: функция страницы читает токен из localStorage страницы (ключ
// "<client_id>:web_token:login:auth") перед каждым вызовом и подставляет его в тело
// запроса — и только туда. Токен не попадает ни в ответ, ни в ошибку, ни в лог; других
// ключей localStorage функция не читает (перебирает только имена ключей). Токен живёт
// недолго — «истёк» (error 5 expired) возвращается кодом token_expired, роутер
// перезагружает вкладку (страница обновит токен сама) и повторяет задачу один раз.
//
// Вся работа в странице — одна самодостаточная функция vkPage: executeScript сериализует её
// через toString(), поэтому никаких импортов и внешних функций.
//
// Ответ VK не уходит как есть: из него вырезаются только поля, которые нужны серверу
// (проекция; сервер проверяет ту же форму — integrations/platforms/vk/extension_wire.py).
// Ссылка на аудио (url трека) в проекцию не входит никогда.

import { z } from "zod";

import { platformInfo, platformTarget } from "../platforms";
import type { OperationDef, PageCall, PageOutcome } from "./registry";

// ------------------------------------------------------------------ в странице

export async function vkPage(call: PageCall): Promise<PageOutcome> {
  const API = "https://web.api.vk.ru/method/";
  const VERSION = "5.289";
  // Пауза между запросами внутри одной операции (детали страницы плейлиста пачками).
  const PAUSE_MS = 350;
  const op = call.op;
  const args = call.args as Record<string, any>;

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
  const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

  const object = (value: unknown): Record<string, any> | undefined =>
    value && typeof value === "object" && !Array.isArray(value)
      ? (value as Record<string, any>)
      : undefined;
  const list = (value: unknown): unknown[] => (Array.isArray(value) ? value : []);
  const text = (value: unknown, max = 500): string | undefined =>
    typeof value === "string" ? value.slice(0, max) : undefined;
  const count = (value: unknown): number | undefined =>
    typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : undefined;
  const int = (value: unknown): number | undefined => {
    const raw = typeof value === "string" && /^-?\d{1,18}$/.test(value) ? Number(value) : value;
    return typeof raw === "number" && Number.isSafeInteger(raw) ? raw : undefined;
  };
  const key = (value: unknown): string | undefined =>
    typeof value === "string" && /^[A-Za-z0-9]{1,64}$/.test(value) ? value : undefined;
  const prune = <T extends Record<string, unknown>>(value: T): T =>
    Object.fromEntries(Object.entries(value).filter(([, v]) => v !== undefined)) as T;
  // executeScript может превратить null в аргументах в undefined — считаем их одним.
  const given = (value: unknown): boolean => value !== null && value !== undefined;

  // Веб-токен страницы — свежий на каждый вызов: страница обновляет его сама. Из
  // localStorage читается только значение ключа токена; перебираются лишь имена ключей.
  function webToken(): { token: string; clientId: string; userId: string | null } {
    let name: string | null = null;
    for (let i = 0; i < localStorage.length; i++) {
      const candidate = localStorage.key(i);
      if (candidate && /^\d{1,20}:web_token:login:auth$/.test(candidate)) {
        name = candidate;
        break;
      }
    }
    const raw = name ? (localStorage.getItem(name) ?? "") : "";
    let token: string | undefined;
    let userId: string | null = null;
    if (raw.startsWith("vk1.")) {
      token = raw;
    } else if (raw) {
      try {
        const stored = object(JSON.parse(raw)) ?? {};
        for (const value of Object.values(stored)) {
          if (typeof value === "string" && value.startsWith("vk1.")) token = value;
        }
        const id = int(stored.user_id ?? stored.userId);
        userId = id !== undefined && id > 0 ? String(id) : null;
      } catch {
        token = undefined;
      }
    }
    if (!name || !token) throw new PageFailure("logged_out", undefined, "нет веб-токена страницы");
    return { token, clientId: name.split(":")[0]!, userId };
  }

  function failure(error: Record<string, any>): PageFailure {
    const code = Number(error.error_code);
    const message = String(error.error_msg ?? "");
    if (code === 14 || code === 17 || "captcha_sid" in error || "redirect_uri" in error) {
      return new PageFailure("captcha", undefined, `VK error ${code}`);
    }
    if (code === 5) {
      return new PageFailure(/expired/i.test(message) ? "token_expired" : "logged_out");
    }
    // 6 — слишком часто (секунды); 9 — flood control, 29 — лимит метода (надолго):
    // длинную паузу и её ступени решает сервер.
    if (code === 6) return new PageFailure("rate_limited", 2);
    if (code === 9) return new PageFailure("rate_limited", 300);
    if (code === 29) return new PageFailure("rate_limited", 3600);
    if ([7, 15, 200, 201, 203].includes(code)) return new PageFailure("not_writable");
    if ([18, 104, 113].includes(code)) return new PageFailure("not_found");
    if (code === 100) return new PageFailure("bad_request", undefined, `VK error ${code}`);
    return new PageFailure("unavailable", undefined, `VK error ${code}`);
  }

  async function api(method: string, params: Record<string, unknown>): Promise<any> {
    const { token, clientId } = webToken();
    const body = new URLSearchParams();
    for (const [name, value] of Object.entries(params)) {
      if (given(value)) body.set(name, String(value));
    }
    body.set("access_token", token);
    let response: Response;
    const abort = new AbortController();
    const timer = setTimeout(() => abort.abort(), 20_000);
    try {
      response = await fetch(`${API}${method}?v=${VERSION}&client_id=${clientId}`, {
        method: "POST",
        body,
        credentials: "include",
        signal: abort.signal,
      });
    } catch (error) {
      throw new PageFailure("unavailable", undefined, `fetch ${method}: ${describe(error)}`);
    } finally {
      clearTimeout(timer);
    }
    const raw = await response.text().catch(() => "");
    if (response.status === 429) throw new PageFailure("rate_limited", 60);
    if (!response.ok) throw new PageFailure("unavailable", undefined, `HTTP ${response.status}`);
    // Страница проверки вместо JSON — человек должен её пройти.
    if (/^\s*</.test(raw)) throw new PageFailure("captcha", undefined, `HTML ${method}`);
    let parsed: Record<string, any> | undefined;
    try {
      parsed = object(JSON.parse(raw));
    } catch {
      throw new PageFailure("unavailable", undefined, `не JSON ${method}`);
    }
    const error = object(parsed?.error);
    if (error) throw failure(error);
    if (!parsed || !("response" in parsed)) {
      throw new PageFailure("unavailable", undefined, `без response ${method}`);
    }
    return parsed.response;
  }

  const names = (value: unknown): string[] =>
    list(value)
      .map((artist) => text(object(artist)?.name))
      .filter((name): name is string => Boolean(name))
      .slice(0, 50);

  function track(value: unknown): Record<string, unknown> | undefined {
    const t = object(value);
    const owner = int(t?.owner_id);
    const id = int(t?.id);
    if (!t || owner === undefined || id === undefined) return undefined;
    const album = object(t.album);
    const thumb = object(album?.thumb) ?? object(t.thumb);
    const release =
      typeof t.release_audio_id === "string" && /^-?\d{1,20}_-?\d{1,20}$/.test(t.release_audio_id)
        ? t.release_audio_id
        : undefined;
    return prune({
      owner_id: owner,
      id,
      access_key: key(t.access_key),
      artist: text(t.artist) ?? "",
      title: text(t.title) ?? "",
      subtitle: text(t.subtitle),
      duration: count(t.duration),
      album_title: text(album?.title),
      cover_url: text(thumb?.photo_600 ?? thumb?.photo_300 ?? thumb?.photo_270, 2000),
      main_artists: names(t.main_artists),
      featured_artists: names(t.featured_artists),
      release_audio_id: release,
    });
  }

  const tracks = (value: unknown): Record<string, unknown>[] =>
    list(value)
      .map(track)
      .filter((t): t is Record<string, unknown> => t !== undefined);

  function playlist(value: unknown): Record<string, unknown> {
    // audio.getPlaylistById отвечает {playlist, profiles} или самим плейлистом.
    const body = object(value);
    const p = object(body?.playlist) ?? body;
    const owner = int(p?.owner_id);
    const id = int(p?.id);
    if (!p || owner === undefined || id === undefined) {
      throw new PageFailure("not_found", undefined, "ответ без плейлиста");
    }
    return prune({
      owner_id: owner,
      id,
      access_key: key(p.access_key),
      title: text(p.title) ?? "",
      description: text(p.description, 5000),
      count: count(p.count) ?? 0,
      type: text(p.type, 20),
      editable: object(p.permissions)?.edit === true,
    });
  }

  let me: { id: string; name: string | null } | null = null;
  async function whoami(): Promise<{ id: string; name: string | null }> {
    if (me === null) {
      const user = object(list(await api("users.get", {}))[0]);
      const id = int(user?.id);
      if (!user || id === undefined || id <= 0) throw new PageFailure("logged_out");
      const name = [text(user.first_name, 100), text(user.last_name, 100)]
        .filter(Boolean)
        .join(" ");
      me = { id: String(id), name: name || null };
    }
    return me;
  }

  try {
    // Сверка аккаунта до операции. Дёшево — по id пользователя в записи веб-токена
    // (если он там есть); иначе — запросом users.get.
    let verified: string | undefined;
    const expected = typeof call.account === "string" && call.account ? call.account : null;
    if (expected !== null) {
      const cheap = call.verify === "cookie" && webToken().userId === expected;
      if (!cheap) {
        const profile = await whoami();
        verified = profile.id;
        if (profile.id !== expected) {
          return { ok: false, code: "session_mismatch", account: profile.id };
        }
      }
    }
    const done = (data: Record<string, unknown>): PageOutcome =>
      verified === undefined ? { ok: true, data } : { ok: true, data, account: verified };
    const own = async (): Promise<string> => expected ?? (await whoami()).id;

    switch (op) {
      case "whoami": {
        const profile = await whoami();
        return {
          ok: true,
          data: prune({ id: profile.id, username: profile.name ?? undefined }),
          account: profile.id,
        };
      }
      case "search": {
        const body = object(await api("audio.search", { q: args.q, count: args.count }));
        return done({ tracks: tracks(body?.items).slice(0, 50) });
      }
      case "tracks": {
        const body = await api("audio.getById", { audios: (args.ids as string[]).join(",") });
        return done({ tracks: tracks(body).slice(0, 100) });
      }
      case "playlist_info": {
        const body = await api("audio.getPlaylistById", {
          owner_id: args.owner_id,
          playlist_id: args.playlist_id,
          access_key: args.access_key,
        });
        return done({ playlist: playlist(body) });
      }
      case "playlist_page": {
        const header = playlist(
          await api("audio.getPlaylistById", {
            owner_id: args.owner_id,
            playlist_id: args.playlist_id,
            access_key: args.access_key,
          }),
        );
        await pause(PAUSE_MS);
        const entity = [args.owner_id, args.playlist_id, header.access_key ?? args.access_key]
          .filter(given)
          .join("_");
        // Пустой плейлист (только что созданный) getIdsBySource может отдать «не найдено» —
        // по шапке видно, что читать нечего.
        const ids =
          header.count === 0
            ? []
            : list(
                object(await api("audio.getIdsBySource", { source: "playlist", entity_id: entity }))
                  ?.audios,
              )
                .map((entry) => text(object(entry)?.audio_id, 200))
                .filter((id): id is string =>
                  Boolean(id && /^-?\d+_-?\d+(_[A-Za-z0-9]+)?$/.test(id)),
                );
        const offset = Number(args.offset ?? 0);
        const end = offset + Number(args.limit);
        const slice = ids.slice(offset, end);
        // Детали — по 100 за запрос; порядок — как в плейлисте.
        const loaded = new Map<string, Record<string, unknown>>();
        for (let start = 0; start < slice.length; start += 100) {
          await pause(PAUSE_MS);
          const body = await api("audio.getById", {
            audios: slice.slice(start, start + 100).join(","),
          });
          for (const t of tracks(body)) loaded.set(`${t.owner_id}_${t.id}`, t);
        }
        const ordered = slice
          .map((id) => loaded.get(id.split("_").slice(0, 2).join("_")))
          .filter((t): t is Record<string, unknown> => t !== undefined);
        return done(
          prune({
            playlist: header,
            total: ids.length,
            tracks: ordered,
            next_offset: end < ids.length ? end : undefined,
          }),
        );
      }
      case "library_page": {
        const offset = Number(args.offset ?? 0);
        const body = object(
          await api("audio.get", { owner_id: await own(), offset, count: args.limit }),
        );
        const items = tracks(body?.items);
        const total = count(body?.count) ?? offset + items.length;
        const next = offset + list(body?.items).length;
        return done(
          prune({
            total,
            tracks: items,
            next_offset: next < total && next > offset ? next : undefined,
          }),
        );
      }
      case "create_playlist": {
        const body = object(
          await api("audio.createPlaylist", {
            owner_id: await own(),
            title: args.title,
            description: args.description,
            no_discover: 1,
          }),
        );
        const owner = int(body?.owner_id);
        const id = int(body?.id);
        if (owner === undefined || id === undefined) return { ok: false, code: "unavailable" };
        return done(prune({ owner_id: owner, id, access_key: key(body?.access_key) }));
      }
      case "set_playlist_tracks": {
        // editPlaylist заменяет и название с описанием — передаём текущие.
        const owner = await own();
        // Ответ — сам плейлист или {playlist, profiles} (с extra_fields), как в playlist().
        const body = object(
          await api("audio.getPlaylistById", { owner_id: owner, playlist_id: args.playlist_id }),
        );
        const header = object(body?.playlist) ?? body;
        if (!header || int(header.id) === undefined) {
          return { ok: false, code: "not_found", detail: "getPlaylistById без плейлиста" };
        }
        await pause(PAUSE_MS);
        await api("audio.editPlaylist", {
          owner_id: owner,
          playlist_id: args.playlist_id,
          title: text(header.title) ?? "",
          description: text(header.description, 5000) ?? "",
          no_discover:
            typeof header.no_discover === "boolean" ? Number(header.no_discover) : undefined,
          audio_ids: (args.audio_ids as string[]).join(","),
        });
        return done({});
      }
      case "add_to_library": {
        const body = await api("audio.add", {
          owner_id: args.owner_id,
          audio_id: args.audio_id,
          access_key: args.access_key,
        });
        // Новый ответ — {items: [{new_owner_id, new_audio_id}], errors}; старый — id числом.
        const added = object(list(object(body)?.items)[0]);
        const owner = int(added?.new_owner_id) ?? Number(await own());
        const id = int(added?.new_audio_id) ?? int(body);
        if (id === undefined || list(object(body)?.errors).length > 0) {
          return { ok: false, code: "bad_request" };
        }
        return done({ owner_id: owner, id });
      }
      default:
        return { ok: false, code: "unavailable" };
    }
  } catch (error) {
    if (error instanceof PageFailure) {
      const result: { ok: false; code: any; retry_after?: number; detail?: string } = {
        ok: false,
        code: error.code,
      };
      if (error.retryAfter !== undefined) result.retry_after = error.retryAfter;
      if (error.detail !== undefined) result.detail = error.detail;
      return result as PageOutcome;
    }
    return { ok: false, code: "unavailable", detail: describe(error) };
  }
}

// ------------------------------------------------------------------ схемы (background)

const MAX_PAGE = 500;
const MAX_TRACKS = 100;
const MAX_SEARCH = 50;

const int = z.number().int().min(-1e18).max(1e18);
const count = z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER);
const key = z.string().regex(/^[A-Za-z0-9]{1,64}$/);
const plain = z.string().regex(/^-?\d{1,20}_-?\d{1,20}$/);
const trackRef = z.string().regex(/^-?\d{1,20}_-?\d{1,20}(_[A-Za-z0-9]{1,64})?$/);
const shortText = z.string().max(500);

const track = z.strictObject({
  owner_id: int,
  id: int,
  access_key: key.optional(),
  artist: shortText,
  title: shortText,
  subtitle: shortText.optional(),
  duration: count.optional(),
  album_title: shortText.optional(),
  cover_url: z.string().max(2000).optional(),
  main_artists: z.array(shortText).max(50),
  featured_artists: z.array(shortText).max(50),
  release_audio_id: plain.optional(),
});

const playlist = z.strictObject({
  owner_id: int,
  id: int,
  access_key: key.optional(),
  title: shortText,
  description: z.string().max(5000).optional(),
  count,
  type: z.string().max(20).optional(),
  editable: z.boolean(),
});

const playlistArgs = {
  owner_id: int,
  playlist_id: int,
  access_key: key.nullable(),
};
const page = { offset: count, limit: z.number().int().min(1).max(MAX_PAGE) };

interface VkOperation {
  name: string;
  write: boolean;
  args: z.ZodType<Record<string, unknown>>;
  result: z.ZodType<Record<string, unknown>>;
  minIntervalMs?: number;
  jitterMs?: number;
}

const OPERATIONS: VkOperation[] = [
  {
    name: "whoami",
    write: false,
    args: z.strictObject({}),
    result: z.strictObject({ id: z.string().regex(/^\d{1,20}$/), username: shortText.optional() }),
  },
  {
    name: "search",
    write: false,
    args: z.strictObject({
      q: z.string().min(1).max(300),
      count: z.number().int().min(1).max(MAX_SEARCH),
    }),
    result: z.strictObject({ tracks: z.array(track).max(MAX_SEARCH) }),
  },
  {
    name: "tracks",
    write: false,
    args: z.strictObject({ ids: z.array(trackRef).min(1).max(MAX_TRACKS) }),
    result: z.strictObject({ tracks: z.array(track).max(MAX_TRACKS) }),
  },
  {
    name: "playlist_info",
    write: false,
    args: z.strictObject(playlistArgs),
    result: z.strictObject({ playlist }),
  },
  {
    name: "playlist_page",
    write: false,
    args: z.strictObject({ ...playlistArgs, ...page }),
    result: z.strictObject({
      playlist,
      total: count,
      tracks: z.array(track).max(MAX_PAGE),
      next_offset: count.optional(),
    }),
  },
  {
    name: "library_page",
    write: false,
    args: z.strictObject(page),
    result: z.strictObject({
      total: count,
      tracks: z.array(track).max(MAX_PAGE),
      next_offset: count.optional(),
    }),
  },
  {
    name: "create_playlist",
    write: true,
    args: z.strictObject({ title: z.string().min(1).max(500), description: z.string().max(5000) }),
    result: z.strictObject({ owner_id: int, id: int, access_key: key.optional() }),
  },
  {
    name: "set_playlist_tracks",
    write: true,
    args: z.strictObject({ playlist_id: int, audio_ids: z.array(plain).max(10_000) }),
    result: z.strictObject({}),
  },
  {
    // «Моя музыка» — бережно: не чаще раза в 2–3 с (flood control VK).
    name: "add_to_library",
    write: true,
    args: z.strictObject({ owner_id: int, audio_id: int, access_key: key.nullable() }),
    result: z.strictObject({ owner_id: int, id: int }),
    minIntervalMs: 2_000,
    jitterMs: 1_000,
  },
];

export function vkOperations(): OperationDef[] {
  const target = platformTarget(platformInfo("vk")!);
  return OPERATIONS.map((operation) => {
    const def: OperationDef = {
      op: `vk.${operation.name}`,
      target,
      write: operation.write,
      session: true,
      args: operation.args,
      result: operation.result,
      main: vkPage,
    };
    if (operation.minIntervalMs !== undefined) def.minIntervalMs = operation.minIntervalMs;
    if (operation.jitterMs !== undefined) def.jitterMs = operation.jitterMs;
    return def;
  });
}
