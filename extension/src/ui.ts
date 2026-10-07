// Мини-хелперы DOM для popup и экрана согласия. Только textContent — никакого innerHTML
// с данными.

import { browser } from "wxt/browser";

import type { PopupRequest, PopupSnapshot } from "./messages";

type Child = Node | string | null | undefined | false;

export function h(
  tag: string,
  props: Record<string, string | boolean | ((event: Event) => void)> = {},
  ...children: Child[]
): HTMLElement {
  const element = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (typeof value === "function") element.addEventListener(key.replace(/^on/, ""), value);
    else if (typeof value === "boolean") element.toggleAttribute(key, value);
    else element.setAttribute(key, value);
  }
  for (const child of children) {
    if (child) element.append(child);
  }
  return element;
}

export function request(message: PopupRequest): Promise<PopupSnapshot> {
  return browser.runtime.sendMessage(message) as Promise<PopupSnapshot>;
}
