import type { Schemas } from "@/shared/api";

export type Transfer = Schemas["TransferResponse"];
export type TransferProgress = Schemas["TransferProgressSchema"];
export type TransferItem = Schemas["TransferItemResponse"];
export type TransferSummary = Schemas["TransferSummaryResponse"];
export type ResolvedLink = Schemas["ResolvedLinkResponse"];
export type StartTransferRequest = Schemas["StartTransferRequest"];

export const transferKeys = {
  all: ["transfers"] as const,
  history: ["transfers", "history"] as const,
  detail: (id: string) => ["transfers", id] as const,
  items: (id: string, statuses: readonly string[]) => ["transfers", id, "items", statuses] as const,
  resolve: (url: string) => ["links", "resolve", url] as const,
};
