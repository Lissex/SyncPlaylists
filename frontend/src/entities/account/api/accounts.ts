import { queryOptions } from "@tanstack/react-query";

import { api, type Schemas } from "@/shared/api";

export type Account = Schemas["AccountResponse"];

export const accountKeys = {
  list: ["accounts"] as const,
};

export const accountsQuery = queryOptions({
  queryKey: accountKeys.list,
  queryFn: () => api.get<Account[]>("/accounts"),
});
