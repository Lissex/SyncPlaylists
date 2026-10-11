import type { Account } from "@/entities/account";
import type { ExtensionStatus } from "@/entities/extension";
import { PLATFORMS, PlatformIcon } from "@/entities/platform";
import { DisconnectAccountButton } from "@/features/disconnect-account";
import { GroupedList } from "@/shared/ui/grouped-list";
import { StatusText } from "@/shared/ui/status-text";

import { connectionState } from "../lib/connection-state";

export function PlatformsPanel({
  accounts,
  extension,
}: {
  accounts: readonly Account[];
  extension: ExtensionStatus | undefined;
}) {
  const online = extension?.devices.some((d) => d.online) ?? false;
  return (
    <GroupedList
      header="Площадки"
      footer="Пароли от площадок мы не храним и не спрашиваем: расширение работает в вашем браузере, где вы уже вошли."
    >
      {PLATFORMS.map((platform) => {
        const session = extension?.platforms.find((p) => p.platform === platform.id);
        const state = connectionState(platform, accounts, session, online);
        return (
          <div key={platform.id} className="flex items-start gap-3 px-4 py-3">
            <PlatformIcon platform={platform.id} className="mt-0.5" />
            <div className="min-w-0 flex-1 space-y-0.5">
              <div className="flex items-baseline justify-between gap-3">
                <span className="min-w-0 text-[17px]">{platform.name}</span>
                <span className="shrink-0 whitespace-nowrap">
                  <StatusText tone={state.tone}>{state.label}</StatusText>
                </span>
              </div>
              {state.hint && <p className="text-[13px] text-label-secondary">{state.hint}</p>}
              {state.account && (
                <div className="pt-1">
                  <DisconnectAccountButton accountId={state.account.id} />
                </div>
              )}
            </div>
          </div>
        );
      })}
    </GroupedList>
  );
}
