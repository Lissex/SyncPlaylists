// Сообщения popup/consent → background (runtime.sendMessage). Background отвечает
// снимком состояния для popup.

import { z } from "zod";

import type { ConnectionState } from "./background/ws-client";
import type { PlatformId } from "./platforms";

export const popupRequest = z.discriminatedUnion("type", [
  z.strictObject({ type: z.literal("get-state") }),
  z.strictObject({ type: z.literal("start-pairing") }),
  z.strictObject({ type: z.literal("poll-pairing") }),
  z.strictObject({ type: z.literal("cancel-pairing") }),
  z.strictObject({ type: z.literal("unpair") }),
  z.strictObject({ type: z.literal("reconnect") }),
]);
export type PopupRequest = z.infer<typeof popupRequest>;

export interface PlatformView {
  id: PlatformId;
  title: string;
  available: boolean;
  granted: boolean;
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
