// Одна фоновая вкладка на цель (площадку): создаётся неактивной при первой задаче,
// переиспользуется следующими и закрывается, когда задач нет idleMs. Вкладку не прячем —
// пользователь видит, что расширение работает с площадкой (автоматизацию не скрываем).
// Id вкладок — в storage.session: service worker могут остановить между задачами.

export interface TabInfo {
  status?: string;
  url?: string;
}

export interface TabsApi {
  create(url: string): Promise<number>;
  get(tabId: number): Promise<TabInfo | null>; // null — вкладки нет (закрыли)
  navigate(tabId: number, url: string): Promise<void>;
  remove(tabId: number): Promise<void>;
  waitComplete(tabId: number, timeoutMs: number): Promise<void>;
}

export interface SessionStore {
  get(): Promise<Record<string, PooledTab>>;
  set(tabs: Record<string, PooledTab>): Promise<void>;
}

export interface PooledTab {
  tabId: number;
  // Когда закрыть, если новых задач не будет (unix ms); null — задача идёт.
  closeAt: number | null;
}

export interface TabPoolOptions {
  idleMs?: number;
  loadTimeoutMs?: number;
  now?: () => number;
  // Разбудить нас к сроку закрытия (alarm — переживает остановку service worker).
  scheduleSweep(at: number): void;
}

export class TabPool {
  private readonly idleMs: number;
  private readonly loadTimeoutMs: number;
  private readonly now: () => number;
  // Чтение-изменение-запись storage.session — по одной (задачи разных целей параллельны).
  private lock: Promise<unknown> = Promise.resolve();

  constructor(
    private readonly tabs: TabsApi,
    private readonly store: SessionStore,
    private readonly options: TabPoolOptions,
  ) {
    this.idleMs = options.idleMs ?? 60_000;
    this.loadTimeoutMs = options.loadTimeoutMs ?? 30_000;
    this.now = options.now ?? Date.now;
  }

  private exclusive<T>(fn: () => Promise<T>): Promise<T> {
    const run = this.lock.catch(() => undefined).then(fn);
    this.lock = run;
    return run;
  }

  // Вкладка цели, загруженная и на нужном сайте. Пока задача идёт, вкладку не закрываем.
  async acquire(key: string, url: string, origins: string[]): Promise<number> {
    const tabId = await this.exclusive(() => this.claim(key, url, origins));
    await this.tabs.waitComplete(tabId, this.loadTimeoutMs);
    return tabId;
  }

  // Только уже открытая вкладка цели, которая сейчас на сайте площадки: новую не
  // открываем и не уводим со страницы. null — такой нет. Занятую вернуть через release.
  acquireExisting(key: string, origins: string[]): Promise<number | null> {
    return this.exclusive(async () => {
      const pooled = await this.store.get();
      const entry = pooled[key];
      if (!entry) return null;
      const info = await this.tabs.get(entry.tabId);
      if (info === null || info.status !== "complete" || !info.url) return null;
      if (!matchesAny(info.url, origins)) return null;
      entry.closeAt = null;
      await this.store.set(pooled);
      return entry.tabId;
    });
  }

  private async claim(key: string, url: string, origins: string[]): Promise<number> {
    const pooled = await this.store.get();
    let tabId = pooled[key]?.tabId;
    const info = tabId === undefined ? null : await this.tabs.get(tabId);
    if (tabId === undefined || info === null) {
      tabId = await this.tabs.create(url);
    } else if (!info.url || !matchesAny(info.url, origins)) {
      // Пользователь ушёл во вкладке на другой сайт — возвращаем её на площадку.
      await this.tabs.navigate(tabId, url);
    }
    pooled[key] = { tabId, closeAt: null };
    await this.store.set(pooled);
    return tabId;
  }

  release(key: string): Promise<void> {
    return this.exclusive(async () => {
      const pooled = await this.store.get();
      const entry = pooled[key];
      if (!entry) return;
      entry.closeAt = this.now() + this.idleMs;
      await this.store.set(pooled);
      this.options.scheduleSweep(entry.closeAt);
    });
  }

  // Закрыть вкладки, срок которых прошёл (по alarm).
  sweep(): Promise<void> {
    return this.exclusive(async () => {
      const pooled = await this.store.get();
      let next: number | null = null;
      for (const [key, entry] of Object.entries(pooled)) {
        if (entry.closeAt === null) continue;
        if (entry.closeAt <= this.now()) {
          delete pooled[key];
          await this.tabs.remove(entry.tabId).catch(() => undefined); // уже закрыта
        } else {
          next = next === null ? entry.closeAt : Math.min(next, entry.closeAt);
        }
      }
      await this.store.set(pooled);
      if (next !== null) this.options.scheduleSweep(next);
    });
  }

  // Пользователь закрыл вкладку сам — следующая задача откроет новую.
  forget(tabId: number): Promise<void> {
    return this.exclusive(async () => {
      const pooled = await this.store.get();
      const key = Object.keys(pooled).find((k) => pooled[k]?.tabId === tabId);
      if (key === undefined) return;
      delete pooled[key];
      await this.store.set(pooled);
    });
  }
}

// Шаблон host permission ("https://soundcloud.com/*") → подходит ли url.
export function matchesAny(url: string, patterns: string[]): boolean {
  return patterns.some((pattern) => url.startsWith(pattern.replace(/\*$/, "")));
}
