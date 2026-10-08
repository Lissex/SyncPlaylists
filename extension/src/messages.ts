// Сообщения popup/consent → background (runtime.sendMessage). Background отвечает
// снимком состояния для popup.

import { z } from "zod";

import type { ConnectRecord, PlatformStatus } from "./background/platform-state";
import type { ConnectionState } from "./background/ws-client";
import type { PlatformId } from "./platforms";
import { PLATFORM_IDS } from "./protocol";

export const popupRequest = z.discriminatedUnion("type", [
  z.strictObject({ type: z.literal("get-state") }),
  z.strictObject({ type: z.literal("start-pairing") }),
  z.strictObject({ type: z.literal("poll-pairing") }),
  z.strictObject({ type: z.literal("cancel-pairing") }),
  z.strictObject({ type: z.literal("unpair") }),
  z.strictObject({ type: z.literal("reconnect") }),
  // Площадка: проверить вход (открывает её вкладку), подключить к аккаунту SyncPlaylists,
  // открыть сайт (войти или пройти проверку — это делает человек).
  z.strictObject({ type: z.literal("check-platform"), platform: z.enum(PLATFORM_IDS) }),
  z.strictObject({ type: z.literal("connect-platform"), platform: z.enum(PLATFORM_IDS) }),
  z.strictObject({ type: z.literal("open-platform"), platform: z.enum(PLATFORM_IDS) }),
]);
export type PopupRequest = z.infer<typeof popupRequest>;

export interface PlatformView {
  id: PlatformId;
  title: string;
  available: boolean;
  granted: boolean;
  operations: boolean; // у площадки уже есть операции в расширении
  status: PlatformStatus | null;
  connect: ConnectRecord | null;
}

export interface PopupSnapshot {
  connection: ConnectionState;
  accountEmail: string | null;
  pairing: { userCode: string; expiresAt: number; intervalMs: number } | null;
  revoked: boolean;
  // Firefox: доступ к серверу SyncPlaylists (обязательный host permission) могли отозвать.
  apiGranted: boolean;
  platforms: PlatformView[];
  pairUrl: string;
  error: string | null;
}
