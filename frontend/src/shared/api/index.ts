import type { components } from "./schema";

export { ApiError, api, apiFetch } from "./client";

/** Схемы ответов бэкенда (сгенерированы из OpenAPI: `pnpm gen:api`). */
export type Schemas = components["schemas"];
