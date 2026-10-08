// Фон расширения: привязка, WebSocket к серверу, исполнение задач в фоновых вкладках.
// Service worker (Chrome, Яндекс Браузер) или event page (Firefox) может быть остановлен
// браузером — всё долгоживущее состояние в storage, а связь поднимают alarms.

import { browser } from "wxt/browser";
import { defineBackground } from "wxt/utils/define-background";

import { Api, ApiError } from "../background/api";
import { Journal } from "../background/journal";
import { Outbox } from "../background/outbox";
import { buildRegistry, type OperationDef } from "../background/registry";
import {
  needsUser,
  readConnects,
  readPaused,
  readStatuses,
  writeConnect,
  writePaused,
  writeStatus,
  type PlatformStatus,
} from "../background/platform-state";
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
import { PLATFORMS, platformInfo, type PlatformInfo, type TargetInfo } from "../platforms";
import type { PlatformIdWire, SessionState } from "../protocol";

const ALARM_KEEPALIVE = "ws-keepalive";
const ALARM_PAIRING = "pairing-poll";
const ALARM_TABS = "tabs-sweep";
const ALARM_JOURNAL = "journal-prune";
const ALARM_RECHECK = "platform-recheck";

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
    existingOnly = false,
  ): Promise<unknown> {
    const tabId = existingOnly
      ? await pool.acquireExisting(target.key, target.origins)
      : await pool.acquire(target.key, target.tabUrl, target.origins);
    if (tabId === null) throw new Error("no_tab");
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
    onMessage(message) {
      if (message.type === "pong") {
        void writePaused(message.paused_platforms ?? []);
      } else if (message.type === "platform_connected") {
        void onConnected(message.platform, true);
      } else if (message.type === "error" && message.request_id?.startsWith("connect:")) {
        const platform = platformInfo(message.request_id.split(":")[1] ?? "");
        if (platform) void onConnected(platform.id, false);
      }
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
    reportState(key, session, account) {
      const info = platformInfo(key);
      if (info) void setStatus(info, session, account, null);
    },
  });

  // ---------------------------------------------------------------- площадки

  const withOperations = (info: PlatformInfo) => registry.has(`${info.id}.whoami`);

  // Новое состояние площадки: сохранить, сообщить серверу (presence; «ok» продолжает
  // переносы на паузе), обновить значок.
  async function setStatus(
    info: PlatformInfo,
    session: SessionState,
    account: string | null,
    username: string | null,
  ): Promise<void> {
    const previous = (await readStatuses())[info.id];
    const sameAccount = account !== null && account === previous?.account;
    const status: PlatformStatus = {
      session,
      account: session === "ok" ? account : null,
      username: username ?? (sameAccount ? (previous?.username ?? null) : null),
      at: Date.now(),
    };
    await writeStatus(info.id, status);
    sendStatus(info, status);
    await updateBadge();
  }

  function sendStatus(info: PlatformInfo, status: PlatformStatus): void {
    ws.send({
      type: "platform_state",
      platform: info.id,
      session: status.session,
      external_user_id: status.session === "ok" ? status.account : null,
    });
  }

  // Кто вошёл на сайте площадки — запросом профиля в её вкладке. existingOnly — только в
  // уже открытой вкладке (фоновые перепроверки), иначе вкладка открывается (действие
  // пользователя). null — проверить не удалось, состояние не меняем.
  async function checkPlatform(
    info: PlatformInfo,
    existingOnly: boolean,
  ): Promise<PlatformStatus | null> {
    const probe = await router.probe(`${info.id}.whoami`, existingOnly);
    if (probe === null) return null;
    if (!probe.ok && probe.detail) {
      // Проверка не дошла до ответа площадки — показываем причину в окне расширения.
      throw new Error(`${info.title}: проверка не удалась (${probe.code}: ${probe.detail})`);
    }
    if (probe.ok) {
      const username = typeof probe.data.username === "string" ? probe.data.username : null;
      await setStatus(info, "ok", String(probe.data.id), username);
    } else if (
      probe.code === "logged_out" ||
      probe.code === "captcha" ||
      probe.code === "no_permission"
    ) {
      await setStatus(info, probe.code, null, null);
    } else {
      throw new Error(`${info.title}: проверка не удалась (${probe.code})`);
    }
    return (await readStatuses())[info.id] ?? null;
  }

  // После welcome сервер должен узнать, готова ли площадка, — иначе переносы через
  // расширение будут ждать. Без разрешения — no_permission сразу. Известное состояние
  // отправляем как есть (аккаунт всё равно сверяется перед каждой задачей), а вкладкой
  // проверяем, только если не знаем ничего: вкладки сами по себе не открываются.
  async function reportPlatforms(): Promise<void> {
    const statuses = await readStatuses();
    for (const info of PLATFORMS.filter((p) => p.available)) {
      const granted = await browser.permissions.contains({ origins: info.origins });
      const known = statuses[info.id];
      if (!granted) {
        if (withOperations(info)) await setStatus(info, "no_permission", null, null);
        else ws.send({ type: "platform_state", platform: info.id, session: "no_permission" });
      } else if (!withOperations(info)) {
        continue; // Яндекс — операции на этапе 4c-4
      } else if (known && known.session !== "no_permission") {
        sendStatus(info, known);
      } else {
        await checkPlatform(info, false);
      }
    }
  }

  // Подключить площадку к аккаунту SyncPlaylists — после явного действия пользователя
  // (выдал разрешение на экране согласия или нажал «Подключить» в popup).
  async function connectPlatform(info: PlatformInfo): Promise<void> {
    const status = await checkPlatform(info, false);
    if (status?.session !== "ok" || !status.account) {
      throw new Error(`Войдите на ${info.title} в этом браузере и повторите`);
    }
    await writeConnect(info.id, { status: "pending", account: status.account });
    const sent = ws.send({
      type: "connect_platform",
      request_id: `connect:${info.id}:${crypto.randomUUID().slice(0, 8)}`,
      platform: info.id,
      external_user_id: status.account,
      display_name: status.username,
    });
    if (!sent) throw new Error("Нет связи с сервером SyncPlaylists");
  }

  async function onConnected(platform: PlatformIdWire, ok: boolean): Promise<void> {
    const record = (await readConnects())[platform];
    if (record) await writeConnect(platform, { ...record, status: ok ? "connected" : "conflict" });
  }

  // Перепроверка площадок, которых ждут переносы на паузе: только вход/аккаунт (капчу
  // человек подтверждает в popup — иначе каждая проверка снова упиралась бы в запись) и
  // только в уже открытой вкладке площадки — новые вкладки ради проверки не открываем.
  async function recheckPlatforms(): Promise<void> {
    const paused = new Set(await readPaused());
    const statuses = await readStatuses();
    for (const info of PLATFORMS.filter((p) => paused.has(p.id) && withOperations(p))) {
      const status = statuses[info.id];
      if (needsUser(status) && status?.session !== "captcha") await checkPlatform(info, true);
    }
  }

  async function updateBadge(): Promise<void> {
    const attention = Object.values(await readStatuses()).some((status) => needsUser(status));
    try {
      await browser.action.setBadgeText({ text: attention ? "!" : "" });
      if (attention) await browser.action.setBadgeBackgroundColor({ color: "#d93025" });
    } catch {
      // Значок — подсказка, без него всё работает.
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
    const statuses = await readStatuses();
    const connects = await readConnects();
    const platforms = await Promise.all(
      PLATFORMS.map(async (p) => ({
        id: p.id,
        title: p.title,
        available: p.available,
        granted: p.available && (await browser.permissions.contains({ origins: p.origins })),
        operations: withOperations(p),
        status: statuses[p.id] ?? null,
        connect: connects[p.id] ?? null,
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

  async function handlePlatform(
    action: "check-platform" | "connect-platform" | "open-platform",
    platform: string,
  ): Promise<void> {
    const info = platformInfo(platform);
    if (!info?.available || !withOperations(info)) return;
    try {
      if (action === "open-platform") {
        // Войти или пройти капчу — в обычной вкладке сайта, это делает человек.
        await browser.tabs.create({ url: info.tabUrl, active: true });
      } else if (action === "connect-platform") {
        await connectPlatform(info);
      } else {
        const status = await checkPlatform(info, false);
        if (status === null)
          throw new Error(`Не удалось открыть ${info.title} — попробуйте ещё раз`);
        // Вошли — сразу подключаем к SyncPlaylists, если ещё не подключено этим аккаунтом
        // (разрешение могли выдать до входа на сайт — тогда подключение не прошло).
        const connected = (await readConnects())[info.id];
        if (status.session === "ok" && connected?.account !== status.account) {
          await connectPlatform(info);
        }
      }
    } catch (error) {
      lastError = error instanceof Error ? error.message : String(error);
    }
  }

  async function handlePopup(request: PopupRequest): Promise<PopupSnapshot> {
    lastError = null;
    try {
      if (
        request.type === "check-platform" ||
        request.type === "connect-platform" ||
        request.type === "open-platform"
      ) {
        await handlePlatform(request.type, request.platform);
        return snapshot();
      }
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
  // Разрешение на сайт площадки выдано на экране согласия — это и есть «подключить»:
  // проверяем вход и подключаем площадку к аккаунту SyncPlaylists.
  browser.permissions.onAdded.addListener((added) => {
    for (const info of PLATFORMS.filter((p) => p.available && withOperations(p))) {
      if (info.origins.some((origin) => added.origins?.includes(origin))) {
        void connectPlatform(info).catch(() => undefined); // popup покажет состояние
      }
    }
  });
  browser.tabs.onRemoved.addListener((tabId) => void pool.forget(tabId));

  browser.alarms.onAlarm.addListener((alarm) => {
    if (alarm.name === ALARM_KEEPALIVE) void ws.ensure();
    else if (alarm.name === ALARM_PAIRING) void pollPairing().catch(() => undefined);
    else if (alarm.name === ALARM_TABS) void pool.sweep();
    else if (alarm.name === ALARM_JOURNAL) void journal.prune();
    else if (alarm.name === ALARM_RECHECK) void recheckPlatforms();
  });

  void browser.alarms.create(ALARM_KEEPALIVE, { periodInMinutes: 0.5 });
  void browser.alarms.create(ALARM_JOURNAL, { periodInMinutes: 360 });
  void browser.alarms.create(ALARM_RECHECK, { periodInMinutes: 2 });
  void updateBadge();
  void pool.sweep();
  void ws.ensure();
});
