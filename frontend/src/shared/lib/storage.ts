// localStorage только для удобств одного браузера (тема, вид ревью). Доступ может бросить
// исключение (приватный режим, запрет cookie) — тогда работаем со значением по умолчанию.
export function readStored(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function writeStored(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // не сохранилось — не страшно
  }
}
