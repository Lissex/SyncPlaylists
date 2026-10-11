import { ArrowRight } from "lucide-react";

import { platformInfo, type Platform } from "../model/platforms";
import { PlatformIcon } from "./platform-icon";

const WHERE: Record<string, string> = {
  library: "медиатека",
  new: "новый плейлист",
  existing: "плейлист",
};

/** «Я Яндекс Музыка → VK VK Музыка · новый плейлист» — откуда и куда. */
export function RouteLine({
  from,
  to,
  destinationKind,
}: {
  from: Platform;
  to: Platform;
  destinationKind: string;
}) {
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5 text-[15px] text-label-secondary">
      <PlatformIcon platform={from} className="size-5 rounded-[5px] text-[8px]" />
      {platformInfo(from).name}
      <ArrowRight className="size-4" aria-label="в" />
      <PlatformIcon platform={to} className="size-5 rounded-[5px] text-[8px]" />
      {platformInfo(to).name}
      {WHERE[destinationKind] && ` · ${WHERE[destinationKind]}`}
    </span>
  );
}

/** Название источника, если площадка его не дала: «Медиатека · VK Музыка». */
export function sourceTitle(
  title: string | null | undefined,
  kind: string,
  platform: Platform,
): string {
  if (title) return title;
  return `${kind === "library" ? "Медиатека" : "Плейлист"} · ${platformInfo(platform).name}`;
}
