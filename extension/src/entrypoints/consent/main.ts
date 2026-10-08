// Экран согласия: отдельная вкладка, а не popup — popup Firefox закрывается, когда
// браузер показывает диалог разрешения, и ответ теряется. permissions.request вызывается
// только по нажатию кнопки (жест пользователя).

import { browser } from "wxt/browser";

import { API_BASE } from "../../config";
import { apiOrigin } from "../../manifest";
import { platformInfo } from "../../platforms";
import { h } from "../../ui";

const app = document.getElementById("app")!;
const target = new URLSearchParams(location.search).get("target") ?? "";

interface Consent {
  title: string;
  origins: string[];
  points: string[];
}

function consentFor(key: string): Consent | null {
  if (key === "api") {
    return {
      title: "Доступ к серверу SyncPlaylists",
      origins: [apiOrigin(API_BASE)],
      points: [
        `Расширение связывается только с сервером SyncPlaylists (${new URL(API_BASE).host}): ` +
          "получает от него задачи переноса и отправляет результаты.",
        "Без этого доступа расширение не может работать.",
      ],
    };
  }
  const platform = platformInfo(key);
  if (!platform || !platform.available) return null;
  return {
    title: `Доступ к сайту «${platform.title}»`,
    origins: platform.origins,
    points: [
      `Когда вы запускаете перенос на сайте SyncPlaylists, расширение открывает ${platform.title} ` +
        "в фоновой вкладке и выполняет там нужные действия: поиск треков, чтение ваших " +
        "плейлистов и лайков, создание плейлистов и добавление треков.",
      "Запросы идут в вашей сессии на сайте площадки. Cookie, токены и пароли площадки " +
        "не покидают браузер: на сервер SyncPlaylists уходят только результаты — названия, " +
        "исполнители, id треков и плейлистов.",
      "Записи на площадку — не чаще одного запроса в секунду. Если площадка попросит капчу, " +
        "её решаете вы — расширение не обходит защиту.",
      "Фоновая вкладка видна среди вкладок и закрывается сама, когда задачи закончились.",
      "Отозвать доступ можно в любой момент — выключателем в окне расширения.",
    ],
  };
}

function render(): void {
  const consent = consentFor(target);
  if (!consent) {
    app.replaceChildren(h("p", { class: "error" }, "Неизвестная площадка."));
    return;
  }
  const status = h("p", {});
  const understood = h("input", { type: "checkbox", id: "understood" }) as HTMLInputElement;
  const grant = h(
    "button",
    {
      class: "primary",
      disabled: true,
      onclick: async () => {
        // Сразу, без await до вызова: request требует жеста пользователя.
        const granted = await browser.permissions.request({ origins: consent.origins });
        status.className = granted ? "" : "error";
        status.textContent = granted
          ? "Доступ выдан. Вкладку можно закрыть."
          : "Доступ не выдан — расширение не будет работать с этим сайтом.";
        if (granted) setTimeout(() => window.close(), 1500);
      },
    },
    "Разрешить",
  ) as HTMLButtonElement;
  understood.addEventListener("change", () => {
    grant.disabled = !understood.checked;
  });
  app.replaceChildren(
    h("h1", {}, consent.title),
    h("ul", {}, ...consent.points.map((point) => h("li", {}, point))),
    h("p", { class: "muted" }, `Будет запрошено разрешение: ${consent.origins.join(", ")}`),
    h("p", {}, h("label", { for: "understood" }, understood, " Понимаю, что делает расширение")),
    grant,
    status,
  );
}

render();
