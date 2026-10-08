// Состояние расширения в storage.local. Popup читает его и подписан на storage.onChanged,
// источник правды — background.

import { browser } from "wxt/browser";

import type { ConnectionState } from "./ws-client";

export interface Pairing {
  pairingId: string; // секрет: по нему расширение забирает токен (в popup не показывается)
  userCode: string;
  expiresAt: number;
  intervalMs: number;
}

export interface StoredState {
  deviceToken?: string;
  deviceId?: string;
  accountEmail?: string;
  pairing?: Pairing;
  connection?: ConnectionState;
  // Отвязано с сайта (сервер закрыл соединение 4401) — popup объясняет, что случилось.
  revoked?: boolean;
}

type Key = keyof StoredState;

export async function readState<K extends Key>(...keys: K[]): Promise<Pick<StoredState, K>> {
  return (await browser.storage.local.get(keys)) as Pick<StoredState, K>;
}

export async function writeState(patch: Partial<StoredState>): Promise<void> {
  await browser.storage.local.set(patch);
}

export async function clearState(...keys: Key[]): Promise<void> {
  await browser.storage.local.remove(keys);
}

// Всё, что связано с привязкой, — при отвязке (журнал и outbox тоже: они от этого аккаунта).
export async function forgetDevice(): Promise<void> {
  const all = await browser.storage.local.get(null);
  const keys = Object.keys(all).filter(
    (key) =>
      key.startsWith("journal:") ||
      ["deviceToken", "deviceId", "accountEmail", "pairing", "outbox"].includes(key),
  );
  await browser.storage.local.remove(keys);
}
