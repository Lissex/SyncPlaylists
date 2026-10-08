// Popup: привязка кодом, статус подключения, площадки (переключатель → экран согласия),
// отвязка. Состояние — у background; popup перерисовывается по storage.onChanged.

import { browser } from "wxt/browser";

import type { PopupSnapshot, PlatformView } from "../../messages";
import { platformInfo } from "../../platforms";
import { h, request } from "../../ui";

const app = document.getElementById("app")!;
const badge = document.getElementById("connection")!;
let pollTimer: ReturnType<typeof setTimeout> | null = null;
let busy = false;

function connectionText(snapshot: PopupSnapshot): [string, string] {
  const state = snapshot.connection;
  switch (state.status) {
    case "online":
      return ["Подключено", "online"];
    case "connecting":
      return ["Подключение…", ""];
    case "offline": {
      const seconds = Math.max(0, Math.round((state.retryAt - Date.now()) / 1000));
      return [`Нет связи, повтор через ${seconds} с`, "offline"];
    }
    case "rejected":
      return ["Сервер не принимает это расширение", "rejected"];
    default:
      return ["Не привязано", ""];
  }
}

async function act(message: Parameters<typeof request>[0]): Promise<void> {
  busy = true;
  try {
    render(await request(message));
  } finally {
    busy = false;
  }
}

function openTab(url: string): void {
  void browser.tabs.create({ url });
  window.close();
}

function consentUrl(target: string): string {
  return browser.runtime.getURL(`/consent.html?target=${encodeURIComponent(target)}`);
}

function unpairedView(snapshot: PopupSnapshot): HTMLElement {
  return h(
    "section",
    {},
    snapshot.revoked &&
      h(
        "p",
        { class: "note" },
        "Расширение отвязано от аккаунта (с сайта или с другого устройства).",
      ),
    h(
      "p",
      {},
      "Привяжите расширение к аккаунту SyncPlaylists: оно покажет код, который нужно ввести на сайте.",
    ),
    h(
      "button",
      { class: "primary", onclick: () => void act({ type: "start-pairing" }), disabled: busy },
      "Привязать к аккаунту",
    ),
  );
}

function pairingView(snapshot: PopupSnapshot): HTMLElement {
  const pairing = snapshot.pairing!;
  const left = Math.max(0, Math.round((pairing.expiresAt - Date.now()) / 1000));
  // Пока popup открыт — опрашиваем сервер с интервалом, который он задал.
  if (pollTimer) clearTimeout(pollTimer);
  pollTimer = setTimeout(() => void act({ type: "poll-pairing" }), pairing.intervalMs);
  return h(
    "section",
    {},
    h("p", {}, "Введите этот код на странице подтверждения SyncPlaylists:"),
    h("div", { class: "code" }, pairing.userCode),
    h(
      "p",
      { class: "muted" },
      `Код действует ещё ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}. `,
      "Никому его не сообщайте.",
    ),
    h(
      "div",
      { class: "row" },
      h(
        "button",
        { class: "primary", onclick: () => openTab(snapshot.pairUrl) },
        "Открыть страницу подтверждения",
      ),
      h("button", { onclick: () => void act({ type: "cancel-pairing" }) }, "Отмена"),
    ),
  );
}

function platformRow(platform: PlatformView): HTMLElement {
  const toggle = h("input", {
    type: "checkbox",
    checked: platform.granted,
    disabled: !platform.available,
    onchange: (event) => {
      const input = event.target as HTMLInputElement;
      if (input.checked) {
        input.checked = false; // включится, когда пользователь даст согласие
        openTab(consentUrl(platform.id));
      } else if (confirm(`Отключить доступ расширения к сайту «${platform.title}»?`)) {
        void (async () => {
          const info = platformInfo(platform.id);
          if (info) await browser.permissions.remove({ origins: info.origins });
          render(await request({ type: "get-state" }));
        })();
      } else {
        input.checked = true;
      }
    },
  });
  return h(
    "li",
    {},
    h("span", {}, platform.title, !platform.available && h("span", { class: "muted" }, " — скоро")),
    toggle,
  );
}

function pairedView(snapshot: PopupSnapshot): HTMLElement {
  return h(
    "section",
    {},
    h("p", {}, "Аккаунт: ", h("b", {}, snapshot.accountEmail ?? "…")),
    !snapshot.apiGranted &&
      h(
        "p",
        { class: "note" },
        "Нет доступа к серверу SyncPlaylists. ",
        h("button", { onclick: () => openTab(consentUrl("api")) }, "Разрешить"),
      ),
    snapshot.connection.status === "rejected" &&
      h(
        "p",
        { class: "note" },
        "Сервер не принимает эту сборку расширения (id нет в EXTENSION__ALLOWED_EXTENSION_IDS).",
      ),
    h("p", { class: "muted" }, "Сайты площадок, с которыми расширению можно работать:"),
    h("ul", { class: "platforms" }, ...snapshot.platforms.map(platformRow)),
    h(
      "div",
      { class: "row" },
      snapshot.connection.status !== "online" &&
        h("button", { onclick: () => void act({ type: "reconnect" }) }, "Переподключить"),
      h(
        "button",
        {
          onclick: () => {
            if (confirm("Отвязать расширение от аккаунта SyncPlaylists?")) {
              void act({ type: "unpair" });
            }
          },
        },
        "Отвязать",
      ),
    ),
  );
}

function render(snapshot: PopupSnapshot): void {
  const [text, kind] = connectionText(snapshot);
  badge.textContent = text;
  badge.className = `badge ${kind}`;
  let view: HTMLElement;
  if (snapshot.connection.status === "unpaired") {
    view = snapshot.pairing ? pairingView(snapshot) : unpairedView(snapshot);
  } else {
    if (pollTimer) clearTimeout(pollTimer);
    view = pairedView(snapshot);
  }
  app.replaceChildren(view, snapshot.error ? h("p", { class: "error" }, snapshot.error) : "");
}

browser.storage.onChanged.addListener(() => {
  if (!busy) void request({ type: "get-state" }).then(render);
});
void request({ type: "get-state" }).then(render);
