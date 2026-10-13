import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { accountKeys } from "@/entities/account";
import { api } from "@/shared/api";
import { Button } from "@/shared/ui/button";

/** Отключить аккаунт площадки (на нашей стороне; на самой площадке ничего не меняется). */
export function DisconnectAccountButton({ accountId }: { accountId: string }) {
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const disconnect = useMutation({
    mutationFn: () => api.delete(`/accounts/${accountId}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: accountKeys.list }),
  });

  if (!confirming) {
    return (
      <Button variant="plain" size="sm" className="text-danger" onClick={() => setConfirming(true)}>
        Отключить
      </Button>
    );
  }
  return (
    <span className="flex items-center gap-3">
      <Button
        variant="plain"
        size="sm"
        className="text-label-secondary"
        onClick={() => setConfirming(false)}
      >
        Отмена
      </Button>
      <Button
        variant="plain"
        size="sm"
        className="text-danger"
        disabled={disconnect.isPending}
        onClick={() => disconnect.mutate()}
      >
        Да, отключить
      </Button>
    </span>
  );
}
