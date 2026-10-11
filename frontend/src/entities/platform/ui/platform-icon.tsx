import { cn } from "@/shared/lib/cn";

import { platformInfo, type Platform } from "../model/platforms";

/** Значок площадки в стиле иконок настроек iOS: цветной квадрат со скруглением. */
export function PlatformIcon({ platform, className }: { platform: Platform; className?: string }) {
  const info = platformInfo(platform);
  const dark = platform === "yandex";
  return (
    <span
      aria-hidden="true"
      style={{ backgroundColor: info.color }}
      className={cn(
        "inline-flex size-8 shrink-0 items-center justify-center rounded-[8px] text-[11px] font-bold",
        dark ? "text-black" : "text-white",
        className,
      )}
    >
      {info.letter}
    </span>
  );
}
