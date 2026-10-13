import { API_BASE } from "@/shared/config";

/**
 * Ошибка API в одном виде для UI. Бэкенд отдаёт `detail` строкой, объектом
 * `{code, message}` или списком ошибок валидации (422 FastAPI).
 */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string | null;
  /** Retry-After в секундах (429: «попробуйте через N»). */
  readonly retryAfter: number | null;
  /** Остальные поля `detail`-объекта (например, `reason` у extension_unavailable). */
  readonly detail: Readonly<Record<string, unknown>>;

  constructor(
    status: number,
    message: string,
    code: string | null,
    retryAfter: number | null,
    detail: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.retryAfter = retryAfter;
    this.detail = detail;
  }
}

const FALLBACK_MESSAGE = "Что-то пошло не так. Попробуйте ещё раз.";

type ParsedDetail = { message: string; code: string | null; extra?: Record<string, unknown> };

function parseDetail(body: unknown): ParsedDetail {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return { message: detail, code: null };
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const { message, code, ...extra } = detail as Record<string, unknown>;
    return {
      message: typeof message === "string" ? message : FALLBACK_MESSAGE,
      code: typeof code === "string" ? code : null,
      extra,
    };
  }
  if (Array.isArray(detail)) return { message: "Проверьте введённые данные", code: "validation" };
  return { message: FALLBACK_MESSAGE, code: null };
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, { ...init, headers, credentials: "include" });
  } catch {
    throw new ApiError(0, "Нет связи с сервером. Проверьте интернет.", "network", null);
  }
  if (response.status === 204) return undefined as T;
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const { message, code, extra } = parseDetail(body);
    const retryAfter = Number(response.headers.get("Retry-After")) || null;
    throw new ApiError(response.status, message, code, retryAfter, extra);
  }
  return body as T;
}

export const api = {
  get: <T>(path: string) => apiFetch<T>(path),
  post: <T>(path: string, body?: unknown) =>
    apiFetch<T>(path, {
      method: "POST",
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  delete: <T = void>(path: string) => apiFetch<T>(path, { method: "DELETE" }),
};
