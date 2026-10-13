import { useQuery } from "@tanstack/react-query";

import { sessionQuery } from "../api/session";

export function useSession() {
  return useQuery(sessionQuery);
}
