import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import { extensionKeys } from "@/entities/extension";
import { ApiError, api } from "@/shared/api";
import { Button } from "@/shared/ui/button";
import { TextField } from "@/shared/ui/text-field";

import { formatPairingCode, isCompleteCode } from "../lib/code";

function pairErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 404)
      return "Код неверный или истёк. Откройте расширение и возьмите новый.";
    if (error.status === 429) return "Слишком много попыток. Подождите минуту.";
    return error.message;
  }
  return "Не удалось привязать. Попробуйте ещё раз.";
}

/**
 * Ввод кода, который показало расширение. Код намеренно не подставляется из ссылки:
 * иначе чужая ссылка с кодом злоумышленника привязала бы его расширение к вашему аккаунту.
 */
export function PairExtensionForm({ onPaired }: { onPaired?: () => void }) {
  const queryClient = useQueryClient();
  const [code, setCode] = useState("");
  const confirm = useMutation({
    mutationFn: (userCode: string) =>
      api.post<void>("/extension/pairings/confirm", { user_code: userCode }),
    onSuccess: () => {
      setCode("");
      // Устройство появится, когда расширение заберёт токен (опрос раз в несколько секунд), —
      // не ждём обычного интервала обновления, перечитываем статус чаще.
      for (const delay of [0, 2_000, 5_000, 10_000]) {
        setTimeout(
          () => void queryClient.invalidateQueries({ queryKey: extensionKeys.status }),
          delay,
        );
      }
      onPaired?.();
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (isCompleteCode(code)) confirm.mutate(code);
  };

  return (
    <form onSubmit={submit} noValidate className="space-y-3">
      <TextField
        label="Код из расширения"
        placeholder="ABCD-EFGH"
        autoComplete="one-time-code"
        autoCapitalize="characters"
        spellCheck={false}
        inputMode="text"
        className="text-center font-mono text-[22px] tracking-[0.2em]"
        value={code}
        onChange={(event) => setCode(formatPairingCode(event.target.value))}
        error={confirm.isError ? pairErrorMessage(confirm.error) : null}
      />
      {confirm.isSuccess && (
        <p role="status" className="px-1 text-[15px] text-success">
          Расширение привязано. Через пару секунд оно появится в списке.
        </p>
      )}
      <Button type="submit" size="block" disabled={!isCompleteCode(code) || confirm.isPending}>
        Привязать
      </Button>
    </form>
  );
}
