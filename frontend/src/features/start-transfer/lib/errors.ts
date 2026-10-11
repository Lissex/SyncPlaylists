import { extensionReasonText } from "@/entities/extension";
import { platformInfo, type Platform } from "@/entities/platform";
import { ApiError } from "@/shared/api";

/**
 * Ошибка разбора ссылки или старта переноса — понятным текстом. Коды — из
 * transfers/presentation/api.py (`_link_or_platform_error`) и разбора ссылок.
 */
export function transferErrorMessage(error: unknown, platform?: Platform): string {
  if (!(error instanceof ApiError)) return "Что-то пошло не так. Попробуйте ещё раз.";
  const site = platform ? platformInfo(platform).site : "сайт площадки";
  switch (error.code) {
    case "invalid_url":
      return "Это не похоже на ссылку. Скопируйте адрес плейлиста целиком.";
    case "unknown_host":
      return "Эту площадку мы пока не знаем. Поддерживаются Яндекс Музыка, VK, SoundCloud, Spotify и YouTube Music.";
    case "not_a_playlist":
      return "Это ссылка не на плейлист (например, на трек или альбом). Нужна ссылка на плейлист или «Мне нравится».";
    case "short_link_unresolved":
      return "Короткая ссылка не открылась. Откройте её в браузере и скопируйте полный адрес.";
    case "too_long":
      return "Ссылка слишком длинная.";
    case "platform_not_supported":
      return "Эта площадка пока не поддерживается — скоро добавим.";
    case "account_not_connected":
      return "Сначала подключите площадку в разделе «Подключения».";
    case "playlist_not_found":
      return "Плейлист не найден или закрыт. Проверьте, что он открыт для всех или принадлежит вам.";
    case "playlist_not_writable":
      return "В этот плейлист добавлять нельзя: он принадлежит другому человеку. Выберите свой или создайте новый.";
    case "write_requires_extension":
      return "Записывать на эту площадку можно только через расширение: подключите её в расширении.";
    case "region_blocked":
      return "Площадка недоступна из региона сервера.";
    case "extension_unavailable":
      return extensionReasonText(readReason(error), site);
    case "platform_unavailable":
      return "Площадка не ответила. Попробуйте через минуту.";
    default:
      return error.message;
  }
}

function readReason(error: ApiError): string | null {
  const reason = error.detail.reason;
  return typeof reason === "string" ? reason : null;
}
