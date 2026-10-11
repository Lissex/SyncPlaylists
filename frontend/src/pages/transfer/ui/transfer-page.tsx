import { LargeTitle } from "@/shared/ui/large-title";

// Прогресс, ревью и отчёт переноса — подэтап 5-5.
export function TransferPage({ transferId }: { transferId: string }) {
  return (
    <LargeTitle subtitle={`Перенос ${transferId.slice(0, 8)} запущен — прогресс появится здесь`}>
      Перенос
    </LargeTitle>
  );
}
