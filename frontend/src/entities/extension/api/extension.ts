import { queryOptions } from "@tanstack/react-query";

import { api, type Schemas } from "@/shared/api";

export type ExtensionStatus = Schemas["ExtensionStatusResponse"];
export type ExtensionDevice = Schemas["DeviceStatusSchema"];
export type PlatformSession = Schemas["PlatformStatusSchema"];
export type SessionState = NonNullable<PlatformSession["state"]>;

export const extensionKeys = {
  status: ["extension", "status"] as const,
};

/** Привязанные расширения и состояние площадок в браузере. Обновляется само: браузер
 *  могут открыть или закрыть в любой момент. */
export const extensionStatusQuery = queryOptions({
  queryKey: extensionKeys.status,
  queryFn: () => api.get<ExtensionStatus>("/extension/status"),
  refetchInterval: 15_000,
});
