import { useMutation, useQueryClient, type InfiniteData } from "@tanstack/react-query";

import { transferKeys, type TransferItem } from "@/entities/transfer";
import { api, type Schemas } from "@/shared/api";

type ItemsPage = Schemas["TransferItemsPage"];

/** Трек снова ждёт распознавания — как его покажет сервер до итога. */
export function markQueued(item: TransferItem): TransferItem {
  return {
    ...item,
    recognition: { state: "queued", note: null, heard: null, can_retry: false },
  };
}

/**
 * «Ещё раз по звуку» на ревью — для трека, который не успели или не смогли распознать.
 * Итог приходит событием переноса (список треков перечитывается сам).
 */
export function useRetryRecognition(transferId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (item: TransferItem) =>
      api.post<void>(`/transfers/${transferId}/items/${item.position}/recognize`, {}),
    onSuccess: (_, item) => {
      const updated = markQueued(item);
      queryClient.setQueriesData<InfiniteData<ItemsPage>>(
        { queryKey: [...transferKeys.detail(transferId), "items"] },
        (data) =>
          data && {
            ...data,
            pages: data.pages.map((page) => ({
              ...page,
              items: page.items.map((i) => (i.position === item.position ? updated : i)),
            })),
          },
      );
    },
  });
}
