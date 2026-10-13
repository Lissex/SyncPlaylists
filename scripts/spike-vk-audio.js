// Спайк 6-0: какое аудио отдаёт VK. Вставить в консоль DevTools на вкладке https://vk.ru/audio
// (вход выполнен). Только чтение: audio.get по своей «Моей музыке» (5 треков), m3u8 каждого и,
// если поток НЕ зашифрован, — Range на первые 1024 байта первого сегмента. Сегменты и ключи
// зашифрованных потоков не запрашиваются (правило этапа 6: шифрованное — только NO_AUDIO).
// В консоль выводятся только хосты, флаги и числа: ни токена, ни URL, ни названий треков.
(async () => {
  const COUNT = 5;
  const name = Object.keys(localStorage).find((k) => /^\d{1,20}:web_token:login:auth$/.test(k));
  if (!name) return console.warn("нет веб-токена страницы — войдите на vk.ru");
  const raw = localStorage.getItem(name) ?? "";
  let token = raw.startsWith("vk1.") ? raw : undefined;
  if (!token) {
    try {
      token = Object.values(JSON.parse(raw)).find((v) => typeof v === "string" && v.startsWith("vk1."));
    } catch {}
  }
  if (!token) return console.warn("веб-токен не разобран");
  const clientId = name.split(":")[0];
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const api = async (method, params) => {
    const body = new URLSearchParams({ ...params, access_token: token });
    const r = await fetch(`https://web.api.vk.ru/method/${method}?v=5.289&client_id=${clientId}`, {
      method: "POST",
      body,
      credentials: "include",
    });
    const json = await r.json();
    if (json.error) throw new Error(`VK error ${json.error.error_code}`);
    return json.response;
  };
  const me = await api("users.get", {});
  await sleep(1000);
  const list = await api("audio.get", { owner_id: me[0].id, count: COUNT });
  const rows = [];
  for (const [i, t] of (list.items ?? []).entries()) {
    const url = typeof t.url === "string" ? t.url : "";
    const row = {
      n: i,
      duration: t.duration,
      official: Boolean(t.main_artists?.length && t.release_audio_id),
      has_url: Boolean(url),
      unavailable: url.includes("audio_api_unavailable"),
      kind: url.includes(".m3u8") ? "m3u8" : url.includes(".mp3") ? "mp3" : url ? "другое" : "-",
      url_host: url ? new URL(url).host : "-",
    };
    if (row.kind === "m3u8" && !row.unavailable) {
      await sleep(1000);
      try {
        const r = await fetch(url);
        const text = await r.text();
        const lines = text.split("\n");
        const keys = lines.filter((l) => l.startsWith("#EXT-X-KEY"));
        const methods = [...new Set(keys.map((k) => /METHOD=([A-Z0-9-]+)/.exec(k)?.[1]))];
        // Сколько сегментов идёт под каждым METHOD (VK бывает чередует NONE и AES-128).
        const perMethod = {};
        let current = "NONE";
        let total = 0;
        const segments = [];
        for (let j = 0; j < lines.length; j++) {
          const l = lines[j];
          if (l.startsWith("#EXT-X-KEY")) current = /METHOD=([A-Z0-9-]+)/.exec(l)?.[1] ?? "?";
          if (l.startsWith("#EXTINF:")) {
            total += parseFloat(l.slice(8));
            perMethod[current] = (perMethod[current] ?? 0) + 1;
            segments.push(new URL(lines[j + 1].trim(), url));
          }
        }
        Object.assign(row, {
          m3u8_status: r.status,
          key_methods: methods.join(",") || "нет",
          segments_per_method: JSON.stringify(perMethod),
          seconds: Math.round(total),
          segment_s: segments.length ? +(total / segments.length).toFixed(1) : 0,
          segment_host: [...new Set(segments.map((s) => s.host))].join(","),
          has_map: lines.some((l) => l.startsWith("#EXT-X-MAP")),
        });
        const encrypted = methods.some((m) => m && m !== "NONE");
        if (!encrypted && segments.length) {
          await sleep(1000);
          const s = await fetch(segments[0], { headers: { Range: "bytes=0-1023" } });
          const body = await s.arrayBuffer();
          Object.assign(row, {
            seg_status: s.status,
            seg_type: s.headers.get("content-type"),
            seg_bytes: body.byteLength,
          });
        } else if (encrypted) {
          row.seg_status = "зашифровано — не запрашиваем";
        }
      } catch (e) {
        row.error = String(e).slice(0, 120);
      }
    }
    rows.push(row);
  }
  console.table(rows);
  console.log("Скопируйте таблицу (правый клик → Copy) или сделайте скриншот.");
})();
