import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ChevronLeft } from "lucide-react";

import { isFinished, transferQuery, useTransferEvents } from "@/entities/transfer";
import { ReviewPanel } from "@/widgets/review-panel";
import { TransferProgressView } from "@/widgets/transfer-progress";
import { TransferReport } from "@/widgets/transfer-report";
import { ApiError } from "@/shared/api";
import { Spinner } from "@/shared/ui/spinner";

/** Перенос: живой прогресс → ревью (если нужно) → отчёт. */
export function TransferPage({ transferId }: { transferId: string }) {
  const transfer = useQuery(transferQuery(transferId));
  const stream = useTransferEvents(transferId, transfer.data?.status);

  const back = (
    <Link to="/transfers" className="inline-flex items-center gap-0.5 text-[17px] text-brand">
      <ChevronLeft className="size-5" aria-hidden="true" />
      История
    </Link>
  );

  if (transfer.isPending) {
    return (
      <div className="space-y-6">
        {back}
        <div className="flex justify-center py-10">
          <Spinner />
        </div>
      </div>
    );
  }
  if (transfer.isError) {
    const notFound = transfer.error instanceof ApiError && transfer.error.status === 404;
    return (
      <div className="space-y-6">
        {back}
        <p role="alert" className="text-danger">
          {notFound ? "Такого переноса нет." : transfer.error.message}
        </p>
      </div>
    );
  }

  const data = transfer.data;
  return (
    <div className="space-y-8">
      {back}
      <TransferProgressView transfer={data} stream={stream} />
      {data.status === "review" && <ReviewPanel transfer={data} />}
      {isFinished(data.status) && <TransferReport transfer={data} />}
    </div>
  );
}
