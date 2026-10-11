/** Как поставить расширение, пока его нет в магазинах: загрузка распакованной сборки. */
export function InstallGuide() {
  return (
    <ol className="list-decimal space-y-2 py-3 pr-4 pl-9 text-[15px] text-label-secondary marker:text-label-tertiary">
      <li>
        Скачайте сборку расширения у администратора сервиса (папка <code>chrome-mv3</code> или{" "}
        <code>firefox-mv3</code>).
      </li>
      <li>
        <b className="font-medium text-label">Chrome, Яндекс Браузер:</b> откройте{" "}
        <code>chrome://extensions</code> (в Яндекс Браузере — <code>browser://extensions</code>),
        включите «Режим разработчика» и нажмите «Загрузить распакованное».
      </li>
      <li>
        <b className="font-medium text-label">Firefox:</b> откройте <code>about:debugging</code> →
        «Этот Firefox» → «Загрузить временное дополнение» и выберите <code>manifest.json</code>.
      </li>
      <li>Нажмите на значок SyncPlaylists в панели браузера — расширение покажет код привязки.</li>
    </ol>
  );
}
