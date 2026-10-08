import { describe, expect, it } from "vitest";

import { matchesAny, type PooledTab, TabPool, type TabsApi } from "../src/background/tabs";

class FakeTabs implements TabsApi {
  tabs = new Map<number, { url: string; status: string }>();
  created: string[] = [];
  removed: number[] = [];
  navigated: Array<[number, string]> = [];
  private next = 1;

  async create(url: string): Promise<number> {
    const id = this.next++;
    this.tabs.set(id, { url, status: "complete" });
    this.created.push(url);
    return id;
  }

  async get(tabId: number) {
    return this.tabs.get(tabId) ?? null;
  }

  async navigate(tabId: number, url: string): Promise<void> {
    this.navigated.push([tabId, url]);
    this.tabs.set(tabId, { url, status: "complete" });
  }

  async remove(tabId: number): Promise<void> {
    this.removed.push(tabId);
    this.tabs.delete(tabId);
  }

  async waitComplete(): Promise<void> {}
}

function setup() {
  const tabs = new FakeTabs();
  let stored: Record<string, PooledTab> = {};
  let now = 1_000;
  const sweeps: number[] = [];
  const pool = new TabPool(
    tabs,
    {
      get: async () => structuredClone(stored),
      set: async (value) => {
        stored = structuredClone(value);
      },
    },
    { idleMs: 60_000, now: () => now, scheduleSweep: (at) => sweeps.push(at) },
  );
  return {
    tabs,
    pool,
    sweeps,
    advance: (ms: number) => {
      now += ms;
    },
  };
}

const SC = {
  key: "soundcloud",
  url: "https://soundcloud.com/",
  origins: ["https://soundcloud.com/*"],
};

describe("фоновые вкладки", () => {
  it("одна вкладка на площадку, переиспользуется", async () => {
    const env = setup();

    const first = await env.pool.acquire(SC.key, SC.url, SC.origins);
    await env.pool.release(SC.key);
    const second = await env.pool.acquire(SC.key, SC.url, SC.origins);

    expect(second).toBe(first);
    expect(env.tabs.created).toEqual([SC.url]);
  });

  it("закрывается после простоя, но не пока идёт задача", async () => {
    const env = setup();
    const tabId = await env.pool.acquire(SC.key, SC.url, SC.origins);

    env.advance(120_000);
    await env.pool.sweep();
    expect(env.tabs.removed).toEqual([]); // задача ещё идёт

    await env.pool.release(SC.key);
    expect(env.sweeps).toEqual([1_000 + 120_000 + 60_000]);
    env.advance(30_000);
    await env.pool.sweep();
    expect(env.tabs.removed).toEqual([]);
    env.advance(30_000);
    await env.pool.sweep();
    expect(env.tabs.removed).toEqual([tabId]);
  });

  it("пользователь закрыл вкладку — следующая задача откроет новую", async () => {
    const env = setup();
    const tabId = await env.pool.acquire(SC.key, SC.url, SC.origins);
    await env.pool.release(SC.key);

    env.tabs.tabs.delete(tabId);
    await env.pool.forget(tabId);
    const next = await env.pool.acquire(SC.key, SC.url, SC.origins);

    expect(next).not.toBe(tabId);
    expect(env.tabs.created).toHaveLength(2);
  });

  it("вкладку увели на другой сайт — возвращаем на площадку", async () => {
    const env = setup();
    const tabId = await env.pool.acquire(SC.key, SC.url, SC.origins);
    await env.pool.release(SC.key);
    env.tabs.tabs.set(tabId, { url: "https://example.com/", status: "complete" });

    await env.pool.acquire(SC.key, SC.url, SC.origins);

    expect(env.tabs.navigated).toEqual([[tabId, SC.url]]);
  });

  it("разные площадки — разные вкладки", async () => {
    const env = setup();

    const [a, b] = await Promise.all([
      env.pool.acquire(SC.key, SC.url, SC.origins),
      env.pool.acquire("yandex", "https://music.yandex.ru/", ["https://music.yandex.ru/*"]),
    ]);

    expect(a).not.toBe(b);
  });

  it("сопоставление url с шаблоном разрешения", () => {
    expect(matchesAny("https://soundcloud.com/you/likes", ["https://soundcloud.com/*"])).toBe(true);
    expect(matchesAny("https://soundcloud.com.evil.example/", ["https://soundcloud.com/*"])).toBe(
      false,
    );
  });
});
