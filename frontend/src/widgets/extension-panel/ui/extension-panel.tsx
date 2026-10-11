import { Monitor } from "lucide-react";

import type { ExtensionStatus } from "@/entities/extension";
import { PairExtensionForm } from "@/features/pair-extension";
import { RevokeDeviceButton } from "@/features/revoke-device";
import { timeAgo } from "@/shared/lib/time";
import { useIsTouchDevice } from "@/shared/lib/use-media-query";
import { GroupedList } from "@/shared/ui/grouped-list";
import { StatusText } from "@/shared/ui/status-text";

import { InstallGuide } from "./install-guide";

export function ExtensionPanel({ status }: { status: ExtensionStatus | undefined }) {
  const touch = useIsTouchDevice();
  const devices = status?.devices ?? [];
  const hasDevices = devices.length > 0;

  return (
    <div className="space-y-8">
      {touch && (
        <p className="rounded-group bg-surface px-4 py-3 text-[15px] text-label-secondary">
          Расширение работает в браузере на компьютере: через него сервис читает и записывает
          плейлисты в вашей сессии. С телефона можно ввести код привязки и следить за переносами.
        </p>
      )}

      {hasDevices && (
        <GroupedList header="Расширение в браузерах">
          {devices.map((device) => (
            <div key={device.id} className="flex items-start gap-3 px-4 py-3">
              <Monitor className="mt-0.5 size-6 shrink-0 text-label-secondary" aria-hidden="true" />
              <div className="min-w-0 flex-1 space-y-0.5">
                <div className="flex items-baseline justify-between gap-3">
                  <span className="min-w-0 truncate text-[17px]">{device.name}</span>
                  <span className="shrink-0 whitespace-nowrap">
                    <StatusText tone={device.online ? "ok" : "muted"}>
                      {device.online ? "На связи" : "Не в сети"}
                    </StatusText>
                  </span>
                </div>
                <div className="truncate text-[13px] text-label-secondary">
                  {device.browser} · {device.version}
                  {!device.online &&
                    device.last_seen_at &&
                    ` · был ${timeAgo(device.last_seen_at)}`}
                </div>
                <div className="pt-1">
                  <RevokeDeviceButton deviceId={device.id} deviceName={device.name} />
                </div>
              </div>
            </div>
          ))}
        </GroupedList>
      )}

      <GroupedList
        header={hasDevices ? "Привязать ещё один браузер" : "Привяжите расширение"}
        footer="Код живёт 5 минут и подходит только один раз."
      >
        <div className="px-4 py-4">
          <PairExtensionForm />
        </div>
        <details className="group" open={!hasDevices && !touch}>
          <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between px-4 text-[17px] text-brand">
            Как установить расширение
            <span
              aria-hidden="true"
              className="text-label-tertiary transition group-open:rotate-90"
            >
              ›
            </span>
          </summary>
          <InstallGuide />
        </details>
      </GroupedList>
    </div>
  );
}
