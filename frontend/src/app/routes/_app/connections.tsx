import { createFileRoute } from "@tanstack/react-router";

import { ConnectionsPage } from "@/pages/connections";

export const Route = createFileRoute("/_app/connections")({ component: ConnectionsPage });
