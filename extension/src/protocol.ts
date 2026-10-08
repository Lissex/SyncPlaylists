// Протокол WebSocket /extension/ws (сервер: modules/extension/presentation/ws.py и
// schemas.py, там тоже extra="forbid"). Все схемы строгие: неизвестное поле — ошибка, а не
// молчаливый пропуск. Исходящие сообщения отправляются только через outgoing.parse —
// случайно попавшее в сообщение поле (cookie, токен площадки) до сервера не дойдёт.

import { z } from "zod";

export const PLATFORM_IDS = ["spotify", "yandex", "vk", "soundcloud", "ytmusic"] as const;
export const SESSION_STATES = ["ok", "logged_out", "captcha", "no_permission"] as const;

// Коды ошибок задачи, которые понимает сервер (operations.error_from_wire); прочие
// сервер считает временной ошибкой площадки.
export const TASK_ERROR_CODES = [
  "logged_out",
  "session_mismatch",
  "captcha",
  "no_permission",
  "not_found",
  "not_writable",
  "rate_limited",
  "unavailable",
  "unsupported_op",
  "bad_args",
  "bad_result",
] as const;
export type TaskErrorCode = (typeof TASK_ERROR_CODES)[number];

// ------------------------------------------------------------------ сервер → расширение

const welcome = z.strictObject({
  type: z.literal("welcome"),
  device_id: z.string().max(64),
  heartbeat_seconds: z.number().int().min(5).max(300),
});

const pong = z.strictObject({ type: z.literal("pong") });

const task = z.strictObject({
  type: z.literal("task"),
  task_id: z.string().min(1).max(64),
  op: z.string().min(1).max(100),
  args: z.record(z.string(), z.unknown()),
  deadline: z.number(),
  idempotency_key: z.string().max(200).nullable(),
});

const platformConnected = z.strictObject({
  type: z.literal("platform_connected"),
  request_id: z.string().max(100),
  platform: z.enum(PLATFORM_IDS),
  account_id: z.string().max(64),
});

const serverError = z.strictObject({
  type: z.literal("error"),
  code: z.string().max(60),
  message: z.string().max(500).optional(),
  request_id: z.string().max(100).optional(),
});

export const incoming = z.discriminatedUnion("type", [
  welcome,
  pong,
  task,
  platformConnected,
  serverError,
]);
export type IncomingMessage = z.infer<typeof incoming>;
export type TaskMessage = z.infer<typeof task>;

// ------------------------------------------------------------------ расширение → сервер

const hello = z.strictObject({
  type: z.literal("hello"),
  // Токен устройства SyncPlaylists (наш, выдан при привязке) — единственный секрет в
  // протоколе. Токены и cookie площадок в протоколе не встречаются никогда.
  token: z.string().min(10).max(200),
  version: z.string().min(1).max(40),
});

const ping = z.strictObject({ type: z.literal("ping") });

const platformState = z.strictObject({
  type: z.literal("platform_state"),
  platform: z.enum(PLATFORM_IDS),
  session: z.enum(SESSION_STATES),
  external_user_id: z.string().min(1).max(200).nullable().optional(),
});

const connectPlatform = z.strictObject({
  type: z.literal("connect_platform"),
  request_id: z.string().min(1).max(100),
  platform: z.enum(PLATFORM_IDS),
  external_user_id: z.string().min(1).max(200),
  display_name: z.string().max(200).nullable().optional(),
});

export const taskError = z.strictObject({
  code: z.enum(TASK_ERROR_CODES),
  message: z.string().max(500).default(""),
  retry_after: z.number().nullable().optional(),
});
export type TaskError = z.infer<typeof taskError>;

const result = z.strictObject({
  type: z.literal("result"),
  task_id: z.string().min(1).max(64),
  ok: z.boolean(),
  data: z.record(z.string(), z.unknown()).optional(),
  error: taskError.optional(),
});

const progress = z.strictObject({
  type: z.literal("progress"),
  task_id: z.string().min(1).max(64),
});

export const outgoing = z.discriminatedUnion("type", [
  hello,
  ping,
  platformState,
  connectPlatform,
  result,
  progress,
]);
export type OutgoingMessage = z.input<typeof outgoing>;
export type ResultMessage = z.input<typeof result>;

// Сервер не примет сообщение больше 2 МБ (ExtensionEndpointConfig.max_message_bytes).
export const MAX_MESSAGE_CHARS = 1_900_000;

export function encodeOutgoing(message: OutgoingMessage): string {
  return JSON.stringify(outgoing.parse(message));
}

export function decodeIncoming(raw: string): IncomingMessage | null {
  try {
    const parsed = incoming.safeParse(JSON.parse(raw));
    return parsed.success ? parsed.data : null;
  } catch {
    return null;
  }
}
