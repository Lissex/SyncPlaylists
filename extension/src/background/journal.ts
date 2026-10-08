// Журнал write-операций в storage.local: результат записи на площадке по ключу
// идемпотентности (create_playlist:<request_id>). Второй рубеж после сервера
// (ext:done в Redis): если ответ потерялся, а сервер повторил задачу, расширение
// вернёт сохранённый результат и второй плейлист не создаст. Срок — как у сервера, сутки.

import { browser } from "wxt/browser";

const PREFIX = "journal:";
export const JOURNAL_TTL_MS = 24 * 60 * 60 * 1000;

interface Entry {
  op: string;
  data: Record<string, unknown>;
  at: number;
}

export class Journal {
  constructor(private readonly now: () => number = Date.now) {}

  async get(key: string): Promise<Record<string, unknown> | null> {
    const stored = await browser.storage.local.get(PREFIX + key);
    const entry = stored[PREFIX + key] as Entry | undefined;
    if (!entry || this.now() - entry.at > JOURNAL_TTL_MS) return null;
    return entry.data;
  }

  async put(key: string, op: string, data: Record<string, unknown>): Promise<void> {
    const entry: Entry = { op, data, at: this.now() };
    await browser.storage.local.set({ [PREFIX + key]: entry });
  }

  async prune(): Promise<void> {
    const all = await browser.storage.local.get(null);
    const stale = Object.entries(all)
      .filter(
        ([key, value]) =>
          key.startsWith(PREFIX) && this.now() - (value as Entry).at > JOURNAL_TTL_MS,
      )
      .map(([key]) => key);
    if (stale.length) await browser.storage.local.remove(stale);
  }
}
