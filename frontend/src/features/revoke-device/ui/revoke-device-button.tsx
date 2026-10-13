import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { extensionKeys } from "@/entities/extension";
import { api } from "@/shared/api";
import { Button } from "@/shared/ui/button";

/** Отзыв устройства в два нажатия: «Отозвать» → «Да, отозвать». */
export function RevokeDeviceButton({
  deviceId,
  deviceName,
}: {
  deviceId: string;
  deviceName: string;
}) {
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const revoke = useMutation({
    mutationFn: () => api.delete(`/extension/devices/${deviceId}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: extensionKeys.status }),
  });

  if (!confirming) {
    return (
      <Button variant="plain" size="sm" className="text-danger" onClick={() => setConfirming(true)}>
        Отозвать
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
        aria-label={`Да, отозвать ${deviceName}`}
        disabled={revoke.isPending}
        onClick={() => revoke.mutate()}
      >
        Да, отозвать
      </Button>
    </span>
  );
}
