import { useMutation, useQueryClient, type InfiniteData } from "@tanstack/react-query";

import { transferKeys, type TransferItem } from "@/entities/transfer";
import { api, type Schemas } from "@/shared/api";

type ItemsPage = Schemas["TransferItemsPage"];
export type Choice = { platform: TransferItem["source_platform"]; externalId: string } | null;

/** Новое состояние трека после решения — как его сохранит сервер (для кэша без перезапроса). */
export function applyChoice(item: TransferItem, choice: Choice): TransferItem {
  if (!choice) return { ...item, status: "skipped", match: null };
  const candidate = item.candidates.find(
    (c) => c.platform === choice.platform && c.external_id === choice.externalId,
  );
  return {
    ...item,
    status: "matched",
    match: {
      target_platform: choice.platform,
      target_external_id: choice.externalId,
      method: "manual",
      score: 1,
      restriction: candidate?.restriction ?? null,
    },
  };
}

/**
 * «Выбрать» / «Пропустить» на ревью. Трек остаётся в списке (с отметкой решения), чтобы
 * решение можно было поменять до «Записать».
 */
export function useResolveItem(transferId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ item, choice }: { item: TransferItem; choice: Choice }) =>
      api.post<void>(
        `/transfers/${transferId}/items/${item.position}/resolve`,
        choice ? { chosen_platform: choice.platform, chosen_external_id: choice.externalId } : {},
      ),
    onSuccess: (_, { item, choice }) => {
      const updated = applyChoice(item, choice);
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
