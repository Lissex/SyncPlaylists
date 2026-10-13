import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "@/shared/api";
import { Button } from "@/shared/ui/button";

/** «Выйти» и «Выйти на всех устройствах». После выхода кэш очищается целиком. */
export function LogoutButtons({ onDone }: { onDone: () => void }) {
  const queryClient = useQueryClient();
  const logout = useMutation({
    mutationFn: (everywhere: boolean) =>
      api.post<void>(everywhere ? "/auth/logout-all" : "/auth/logout"),
    onSuccess: () => {
      queryClient.clear();
      onDone();
    },
  });
  return (
    <div className="space-y-2">
      <Button
        variant="destructive"
        size="block"
        disabled={logout.isPending}
        onClick={() => logout.mutate(false)}
      >
        Выйти
      </Button>
      <Button
        variant="plain"
        className="w-full py-2 text-danger"
        disabled={logout.isPending}
        onClick={() => logout.mutate(true)}
      >
        Выйти на всех устройствах
      </Button>
      {logout.isError && (
        <p role="alert" className="text-center text-[13px] text-danger">
          {logout.error.message}
        </p>
      )}
    </div>
  );
}
