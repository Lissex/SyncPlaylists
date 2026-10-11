import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { API_BASE } from "@/shared/config";

import { transferKeys, type Transfer, type TransferProgress } from "../api/transfer";
import { isFinished } from "../lib/status";

// События, после которых меняется шапка (статус, плейлисты, счётчики записи) — её
// перечитываем; частые TrackMatched/TrackNeedsReview покрывает `progress` раз в 3 с.
const HEADER_EVENTS = new Set([
  "TransferStarted",
  "TransferWritingStarted",
  "TransferCompleted",
  "TransferFailed",
  "TransferPausedForQuota",
  "TransferPausedForClient",
  "TransferResumed",
  "CaptchaRequired",
]);

export type TransferStream = {
  /** Соединение открыто (иначе — переподключаемся, данные могут отставать). */
  live: boolean;
  /** Причина из события TransferFailed (в шапке её нет). */
  failureReason: string | null;
};

/**
 * Живой прогресс переноса по SSE (`/transfers/{id}/events`): `progress` — счётчики и ETA
 * раз в 3 с, `domain_event` — события домена. Пишет прямо в кэш шапки переноса.
 * EventSource сам переподключается; после done/failed поток закрывается.
 */
export function useTransferEvents(transferId: string, status: string | undefined): TransferStream {
  const queryClient = useQueryClient();
  const [live, setLive] = useState(false);
  const [failureReason, setFailureReason] = useState<string | null>(null);
  const finished = status !== undefined && isFinished(status);

  useEffect(() => {
    if (finished) return;
    const key = transferKeys.detail(transferId);
    const source = new EventSource(`${API_BASE}/transfers/${transferId}/events`, {
      withCredentials: true,
    });
    source.onopen = () => setLive(true);
    source.onerror = () => setLive(false);

    source.addEventListener("progress", (event) => {
      const progress = JSON.parse((event as MessageEvent<string>).data) as TransferProgress;
      queryClient.setQueryData<Transfer>(key, (current) => {
        if (!current) return current;
        if (current.status !== progress.status)
          void queryClient.invalidateQueries({ queryKey: key });
        return { ...current, status: progress.status, progress };
      });
    });

    source.addEventListener("domain_event", (event) => {
      const payload = JSON.parse((event as MessageEvent<string>).data) as {
        type?: string;
        reason?: string;
      };
      if (payload.type === "TransferFailed") setFailureReason(payload.reason ?? null);
      if (payload.type && HEADER_EVENTS.has(payload.type)) {
        void queryClient.invalidateQueries({ queryKey: key });
      }
    });

    return () => {
      source.close();
      setLive(false);
    };
  }, [transferId, finished, queryClient]);

  return { live, failureReason };
}
