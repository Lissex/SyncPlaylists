import { createFileRoute } from "@tanstack/react-router";

import { NewTransferPage } from "@/pages/new-transfer";

export const Route = createFileRoute("/_app/")({ component: NewTransferPage });
