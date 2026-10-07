// Фон расширения: привязка, WebSocket к серверу, исполнение задач в фоновых вкладках.
// Service worker (Chrome, Яндекс Браузер) или event page (Firefox) может быть остановлен
// браузером — всё долгоживущее состояние в storage, а связь поднимают alarms.

import { browser } from "wxt/browser";
import { defineBackground } from "wxt/utils/define-background";

import { Api, ApiError } from "../background/api";
import { Journal } from "../background/journal";
import { Outbox } from "../background/outbox";
import { buildRegistry, type OperationDef } from "../background/registry";
import { TaskRouter } from "../background/router";
import {
  clearState,
  forgetDevice,
  readState,
  writeState,
  type StoredState,
} from "../background/state";
import { TabPool, type PooledTab, type TabsApi } from "../background/tabs";
import { type ConnectionState, type SocketLike, WsClient } from "../background/ws-client";
import { API_BASE, DIAGNOSTICS, PAIR_URL, WS_URL } from "../config";
import { apiOrigin } from "../manifest";
import { type PopupRequest, type PopupSnapshot, popupRequest } from "../messages";
import { PLATFORMS, type TargetInfo } from "../platforms";

const ALARM_KEEPALIVE = "ws-keepalive";
const ALARM_PAIRING = "pairing-poll";
const ALARM_TABS = "tabs-sweep";
const ALARM_JOURNAL = "journal-prune";

export default defineBackground(() => {
  const api = new Api(API_BASE);
  const journal = new Journal();
  const outbox = new Outbox();
  const registry = buildRegistry({ apiBase: API_BASE, diagnostics: DIAGNOSTICS });
  let lastError: string | null = null;

  const tabsApi: TabsApi = {
    async create(url) {
      const tab = await browser.tabs.create({ url, active: false });
      if (tab.id === undefined) throw new Error("tab");
      // Площадка может начать играть музыку — фоновая вкладка без звука.
      await browser.tabs.update(tab.id, { muted: true }).catch(() => undefined);
      return tab.id;
    },
    async get(tabId) {
      try {
        const tab = await browser.tabs.get(tabId);
        return { status: tab.status, url: tab.url };
      } catch {
        return null;
      }
    },
    async navigate(tabId, url) {
      await browser.tabs.update(tabId, { url });
    },
    async remove(tabId) {
      await browser.tabs.remove(tabId);
    },
    waitComplete(tabId, timeoutMs) {
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => finish(new Error("tab_timeout")), timeoutMs);
        const listener = (id: number, info: { status?: string }) => {
          if (id === tabId && info.status === "complete") finish();
        };
        const finish = (error?: Error) => {
          clearTimeout(timer);
          browser.tabs.onUpdated.removeListener(listener);
          if (error) reject(error);
          else resolve();
        };
        browser.tabs.onUpdated.addListener(listener);
        browser.tabs.get(tabId).then(
          (tab) => tab.status === "complete" && finish(),
          () => finish(new Error("tab_closed")),
        );
      });
    },
  };

  const pool = new TabPool(
    tabsApi,
    {
      async get() {
        const stored = await browser.storage.session.get("tabs");
        return (stored.tabs as Record<string, PooledTab> | undefined) ?? {};
      },
      async set(tabs) {
        await browser.storage.session.set({ tabs });
      },
    },
    {
      scheduleSweep(at) {
        void browser.alarms.create(ALARM_TABS, { when: at });
      },
    },
  );

  async function runInTarget(
    target: TargetInfo,
    main: OperationDef["main"],
    args: unknown,
  ): Promise<unknown> {
    const tabId = await pool.acquire(target.key, target.tabUrl, target.origins);
    try {
      const results = await browser.scripting.executeScript({
        target: { tabId },
        world: "MAIN",
        func: main as (args: unknown) => unknown,
        args: [args],
      });
      return results[0]?.result;
    } finally {
      await pool.release(target.key);
    }
  }

  const ws = new WsClient({
    url: WS_URL,
    version: browser.runtime.getManifest().version,
    makeSocket: (url) => new WebSocket(url) as unknown as SocketLike,
    async getToken() {
      return (await readState("deviceToken")).deviceToken ?? null;
    },
    async onRevoked() {
      await forgetDevice();
      await writeState({ revoked: true });
    },
    onState(state: ConnectionState) {
      void writeState({ connection: state });
    },
    onWelcome() {
      void outbox.flush((message) => ws.send(message));
      void reportPlatforms();
      void refreshAccount();
    },
    onTask(task) {
      void router.handle(task);
    },
  });

  const router = new TaskRouter({
    registry,
    journal,
    outbox,
    hasPermission: (origins) => browser.permissions.contains({ origins }),
    runInTarget,
    sendResult: (message) => ws.send(message),
    sendProgress: (taskId) => {
      ws.send({ type: "progress", task_id: taskId });
    },
  });

  // Без разрешения на сайт площадки задачи там выполнить нельзя — сервер узнаёт об этом
  // сразу (перенос встанет на паузу no_permission), а не по таймауту. Вход на площадке и
  // её аккаунт (platform_state ok) проверяют модули площадок — этапы 4c-3/4c-4.
  async function reportPlatforms(): Promise<void> {
    for (const platform of PLATFORMS.filter((p) => p.available)) {
      const granted = await browser.permissions.contains({ origins: platform.origins });
      if (!granted) {
        ws.send({ type: "platform_state", platform: platform.id, session: "no_permission" });
      }
    }
  }

  async function refreshAccount(): Promise<void> {
    const { deviceToken } = await readState("deviceToken");
    if (!deviceToken) return;
    try {
      const me = await api.me(deviceToken);
      if (me === null) {
        ws.stop();
        await forgetDevice();
        await writeState({ revoked: true, connection: { status: "unpaired" } });
      } else {
        await writeState({ accountEmail: me.email, deviceId: me.deviceId });
      }
    } catch {
      // Сеть — email покажем в следующий раз.
    }
  }

  // ---------------------------------------------------------------- привязка

  function browserName(): string {
    if (navigator.userAgent.includes("YaBrowser")) return "yandex";
    return import.meta.env.BROWSER === "firefox" ? "firefox" : "chrome";
  }

  function deviceName(): string {
    const titles: Record<string, string> = {
      yandex: "Яндекс Браузер",
      firefox: "Firefox",
      chrome: "Chrome",
    };
    const ua = navigator.userAgent;
    const os = ua.includes("Windows")
      ? "Windows"
      : ua.includes("Mac OS")
        ? "macOS"
        : ua.includes("Android")
          ? "Android"
          : ua.includes("Linux")
            ? "Linux"
            : "";
    return [titles[browserName()], os].filter(Boolean).join(" · ");
  }

  async function startPairing(): Promise<void> {
    const started = await api.startPairing({
      device_name: deviceName(),
      browser: browserName(),
      version: browser.runtime.getManifest().version,
    });
    await writeState({
      revoked: false,
      pairing: {
        pairingId: started.pairing_id,
        userCode: started.user_code,
        expiresAt: Date.now() + started.expires_in * 1000,
        intervalMs: started.interval * 1000,
      },
    });
    // Popup опрашивает сам, пока открыт; alarm — если popup закрыли.
    await browser.alarms.create(ALARM_PAIRING, { periodInMinutes: 0.5 });
  }

  async function stopPairing(): Promise<void> {
    await clearState("pairing");
    await browser.alarms.clear(ALARM_PAIRING);
  }

  async function pollPairing(): Promise<void> {
    const { pairing } = await readState("pairing");
    if (!pairing) return stopPairing();
    if (Date.now() > pairing.expiresAt) return stopPairing();
    const claim = await api.claimPairing(pairing.pairingId);
    if (claim.status === "pending") return;
    await stopPairing();
    if (claim.status === "paired") {
      await writeState({ deviceToken: claim.deviceToken, deviceId: claim.deviceId });
      await ws.restart();
    }
  }

  async function unpair(): Promise<void> {
    const { deviceToken } = await readState("deviceToken");
    if (deviceToken) await api.unpair(deviceToken);
    ws.stop();
    await forgetDevice();
    await writeState({ revoked: false, connection: { status: "unpaired" } });
  }

  // ---------------------------------------------------------------- popup

  async function snapshot(): Promise<PopupSnapshot> {
    const state: StoredState = await readState(
      "connection",
      "accountEmail",
      "pairing",
      "revoked",
      "deviceToken",
    );
    const platforms = await Promise.all(
      PLATFORMS.map(async (p) => ({
        id: p.id,
        title: p.title,
        available: p.available,
        granted: p.available && (await browser.permissions.contains({ origins: p.origins })),
      })),
    );
    return {
      connection: state.deviceToken
        ? (state.connection ?? { status: "connecting" })
        : { status: "unpaired" },
      accountEmail: state.accountEmail ?? null,
      pairing: state.pairing
        ? {
            userCode: state.pairing.userCode,
            expiresAt: state.pairing.expiresAt,
            intervalMs: state.pairing.intervalMs,
          }
        : null,
      revoked: state.revoked ?? false,
      apiGranted: await browser.permissions.contains({ origins: [apiOrigin(API_BASE)] }),
      platforms,
      pairUrl: PAIR_URL,
      error: lastError,
    };
  }

  async function handlePopup(request: PopupRequest): Promise<PopupSnapshot> {
    lastError = null;
    try {
      if (request.type === "start-pairing") await startPairing();
      else if (request.type === "poll-pairing") await pollPairing();
      else if (request.type === "cancel-pairing") await stopPairing();
      else if (request.type === "unpair") await unpair();
      else if (request.type === "reconnect") await ws.restart();
    } catch (error) {
      lastError =
        error instanceof ApiError
          ? `Сервер ответил ${error.status}`
          : "Нет связи с сервером SyncPlaylists";
    }
    return snapshot();
  }

  browser.runtime.onMessage.addListener((message, sender, sendResponse) => {
    // Только страницы самого расширения (popup, экран согласия).
    if (sender.id !== browser.runtime.id || !sender.url?.startsWith(browser.runtime.getURL("/"))) {
      return false;
    }
    const request = popupRequest.safeParse(message);
    if (!request.success) return false;
    void handlePopup(request.data).then(sendResponse);
    return true; // ответ асинхронный
  });

  // ---------------------------------------------------------------- события браузера

  browser.permissions.onRemoved.addListener(() => void reportPlatforms());
  browser.tabs.onRemoved.addListener((tabId) => void pool.forget(tabId));

  browser.alarms.onAlarm.addListener((alarm) => {
    if (alarm.name === ALARM_KEEPALIVE) void ws.ensure();
    else if (alarm.name === ALARM_PAIRING) void pollPairing().catch(() => undefined);
    else if (alarm.name === ALARM_TABS) void pool.sweep();
    else if (alarm.name === ALARM_JOURNAL) void journal.prune();
  });

  void browser.alarms.create(ALARM_KEEPALIVE, { periodInMinutes: 0.5 });
  void browser.alarms.create(ALARM_JOURNAL, { periodInMinutes: 360 });
  void pool.sweep();
  void ws.ensure();
});
