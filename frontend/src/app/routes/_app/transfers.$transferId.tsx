import { createFileRoute } from "@tanstack/react-router";

import { TransferPage } from "@/pages/transfer";

export const Route = createFileRoute("/_app/transfers/$transferId")({
  component: function Page() {
    const { transferId } = Route.useParams();
    return <TransferPage transferId={transferId} />;
  },
});
