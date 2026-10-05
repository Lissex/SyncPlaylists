"""Имена фоновых задач, которые ставит один контекст, а выполняет другой. Задачи
связывают контексты только по имени (TaskQueue.enqueue), без импорта чужого кода."""

from typing import Final

# Расширение пользователя снова готово работать с площадкой (контекст extension) →
# продолжить переносы, ждущие браузер (контекст transfers).
# Аргументы: user_id (UUID), platform (Platform.value).
RESUME_CLIENT_TRANSFERS: Final = "resume_client_transfers"
