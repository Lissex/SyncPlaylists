import { useQuery } from "@tanstack/react-query";

import { accountsQuery } from "@/entities/account";
import { extensionStatusQuery } from "@/entities/extension";
import { ExtensionPanel } from "@/widgets/extension-panel";
import { PlatformsPanel } from "@/widgets/platforms-panel";
import { LargeTitle } from "@/shared/ui/large-title";
import { Spinner } from "@/shared/ui/spinner";

export function ConnectionsPage() {
  const extension = useQuery(extensionStatusQuery);
  const accounts = useQuery(accountsQuery);
  const error = extension.error ?? accounts.error;

  return (
    <div className="space-y-8">
      <LargeTitle subtitle="Расширение в браузере и музыкальные площадки">Подключения</LargeTitle>
      {extension.isPending || accounts.isPending ? (
        <div className="flex justify-center py-10">
          <Spinner />
        </div>
      ) : error ? (
        <p role="alert" className="text-danger">
          {error.message}
        </p>
      ) : (
        <>
          <ExtensionPanel status={extension.data} />
          <PlatformsPanel accounts={accounts.data ?? []} extension={extension.data} />
        </>
      )}
    </div>
  );
}
