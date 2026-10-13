// Все запросы к бэкенду — через один префикс: в dev его снимает прокси Vite, в Docker — nginx.
export const API_BASE = "/api";
