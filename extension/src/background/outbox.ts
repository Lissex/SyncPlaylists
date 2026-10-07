// Результаты, которые не удалось отправить (WebSocket закрылся, пока операция шла).
// Досылаются после следующего welcome: сервер примет результат задачи, выданной этому
// устройству, и сохранит его по ключу идемпотентности (4c-1).

import { browser } from "wxt/browser";

import type { ResultMessage } from "../protocol";

const KEY = "outbox";
const TTL_MS = 24 * 60 * 60 * 1000;
const MAX_ITEMS = 100;

interface Pending {
  message: ResultMessage;
  at: number;
}

export class Outbox {
  constructor(private readonly now: () => number = Date.now) {}

  private async load(): Promise<Pending[]> {
    const stored = await browser.storage.local.get(KEY);
    const items = (stored[KEY] as Pending[] | undefined) ?? [];
    return items.filter((item) => this.now() - item.at <= TTL_MS);
  }

  async put(message: ResultMessage): Promise<void> {
    const items = await this.load();
    items.push({ message, at: this.now() });
    await browser.storage.local.set({ [KEY]: items.slice(-MAX_ITEMS) });
  }

  // send → false: соединения снова нет, остаток ждёт следующего раза.
  async flush(send: (message: ResultMessage) => boolean): Promise<void> {
    const items = await this.load();
    const left: Pending[] = [];
    for (const item of items) {
      if (left.length || !send(item.message)) left.push(item);
    }
    await browser.storage.local.set({ [KEY]: left });
  }
}
