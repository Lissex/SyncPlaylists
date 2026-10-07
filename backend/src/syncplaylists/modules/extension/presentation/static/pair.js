// Dev-страница привязки расширения (этап 4c-2). Ходит в API того же origin с cookie сессии.
"use strict";

const $ = (id) => document.getElementById(id);

async function api(method, path, body) {
  const response = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try {
    data = await response.json();
  } catch {
    // 204 и пустые ответы
  }
  return { status: response.status, data };
}

function detail(data) {
  const d = data && data.detail;
  if (!d) return "";
  if (typeof d === "string") return d;
  if (d.message) return d.message;
  return JSON.stringify(d);
}

function setStatus(id, text, ok) {
  const el = $(id);
  el.textContent = text;
  el.className = ok === undefined ? "muted" : ok ? "ok" : "err";
}

async function refreshSession() {
  const me = await api("GET", "/auth/me");
  const loggedIn = me.status === 200;
  $("login-section").hidden = loggedIn;
  $("account-section").hidden = !loggedIn;
  if (loggedIn) {
    $("who").textContent = me.data.email;
    await refreshDevices();
  }
}

async function credentials(path) {
  const result = await api("POST", path, { email: $("email").value, password: $("password").value });
  if (result.status === 200 || result.status === 201) {
    setStatus("login-status", "");
    await refreshSession();
  } else {
    setStatus("login-status", detail(result.data) || `Ошибка ${result.status}`, false);
  }
}

function cell(row, text) {
  const td = document.createElement("td");
  td.textContent = text;
  row.appendChild(td);
  return td;
}

function button(parent, text, onClick) {
  const b = document.createElement("button");
  b.type = "button";
  b.textContent = text;
  b.addEventListener("click", onClick);
  parent.appendChild(b);
  return b;
}

async function refreshDevices() {
  const result = await api("GET", "/extension/devices");
  const body = $("devices");
  body.replaceChildren();
  if (result.status !== 200) {
    setStatus("devices-status", `Не удалось получить список: ${result.status}`, false);
    return;
  }
  setStatus("devices-status", result.data.length ? "" : "Пока ни одного.");
  for (const device of result.data) {
    const row = document.createElement("tr");
    cell(row, `${device.name} · ${device.browser} ${device.version}${device.revoked ? " (отозвано)" : ""}`);
    cell(row, device.last_seen_at ? new Date(device.last_seen_at).toLocaleString() : "—");
    const actions = cell(row, "");
    if (!device.revoked) {
      const probeStatus = document.createElement("div");
      probeStatus.className = "muted";
      button(actions, "Проверить связь", () => probe(device.id, probeStatus));
      button(actions, "Отозвать", async () => {
        const revoked = await api("DELETE", `/extension/devices/${device.id}`);
        if (revoked.status !== 204) alert(`Ошибка ${revoked.status}`);
        await refreshDevices();
      });
      actions.appendChild(probeStatus);
    }
    body.appendChild(row);
  }
}

async function probe(deviceId, statusEl) {
  statusEl.className = "muted";
  statusEl.textContent = "Проба отправлена…";
  const started = await api("POST", `/extension/devices/${deviceId}/probe`);
  if (started.status !== 202) {
    statusEl.className = "err";
    statusEl.textContent = detail(started.data) || `Ошибка ${started.status}`;
    return;
  }
  const until = Date.now() + 45_000;
  while (Date.now() < until) {
    await new Promise((resolve) => setTimeout(resolve, 1000));
    const result = await api("GET", `/extension/probes/${started.data.probe_id}`);
    if (result.status !== 200) continue;
    if (result.data.status === "ok") {
      statusEl.className = "ok";
      statusEl.textContent = `OK: вкладка «${result.data.data.page_title}» ответила, nonce совпал`;
      return;
    }
    if (result.data.status === "error") {
      statusEl.className = "err";
      statusEl.textContent = `Ошибка: ${result.data.error}`;
      return;
    }
  }
  statusEl.className = "err";
  statusEl.textContent = "Нет ответа (воркер worker-extension запущен?)";
}

$("login-form").addEventListener("submit", (event) => {
  event.preventDefault();
  credentials("/auth/login");
});
$("register").addEventListener("click", () => credentials("/auth/register"));
$("logout").addEventListener("click", async () => {
  await api("POST", "/auth/logout");
  await refreshSession();
});
$("refresh").addEventListener("click", refreshDevices);
$("code-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const code = $("code").value.trim().toUpperCase();
  const result = await api("POST", "/extension/pairings/confirm", { user_code: code });
  if (result.status === 204) {
    setStatus("code-status", "Готово: расширение получит доступ в течение нескольких секунд.", true);
    $("code").value = "";
    setTimeout(refreshDevices, 4000);
  } else {
    setStatus("code-status", detail(result.data) || `Ошибка ${result.status}`, false);
  }
});

refreshSession();
