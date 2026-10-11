import { useNavigate } from "@tanstack/react-router";

import { StartTransferForm } from "@/features/start-transfer";
import { LargeTitle } from "@/shared/ui/large-title";

export function NewTransferPage() {
  const navigate = useNavigate();
  return (
    <div className="space-y-8">
      <LargeTitle subtitle="Вставьте ссылку на плейлист — найдём треки на другой площадке">
        Перенос
      </LargeTitle>
      <StartTransferForm
        onStarted={(id) => navigate({ to: "/transfers/$transferId", params: { transferId: id } })}
      />
    </div>
  );
}
