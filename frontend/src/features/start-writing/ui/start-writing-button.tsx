import { useMutation, useQueryClient } from "@tanstack/react-query";

import { transferKeys } from "@/entities/transfer";
import { ApiError, api } from "@/shared/api";
import { Button } from "@/shared/ui/button";

/** «Записать в <площадку>» — запуск записи после ревью (сама она не начинается). */
export function StartWritingButton({
  transferId,
  platformName,
  undecided,
}: {
  transferId: string;
  platformName: string;
  /** Сколько сомнительных ещё не решено — их пропустим. */
  undecided: number;
}) {
  const queryClient = useQueryClient();
  const write = useMutation({
    mutationFn: () => api.post<void>(`/transfers/${transferId}/write`),
    onSettled: () => queryClient.invalidateQueries({ queryKey: transferKeys.detail(transferId) }),
  });
  const alreadyWriting = write.error instanceof ApiError && write.error.status === 409;

  return (
    <div className="space-y-2">
      <Button
        size="block"
        disabled={write.isPending || write.isSuccess}
        onClick={() => write.mutate()}
      >
        Записать в {platformName}
      </Button>
      <p className="text-center text-[13px] text-label-secondary">
        {undecided > 0
          ? `Не решённые сомнительные (${undecided}) пропустим. Ненайденные останутся в отчёте.`
          : "Ненайденные треки останутся в отчёте — их можно будет добавить вручную."}
      </p>
      {write.isError && !alreadyWriting && (
        <p role="alert" className="text-center text-[15px] text-danger">
          {write.error.message}
        </p>
      )}
    </div>
  );
}
