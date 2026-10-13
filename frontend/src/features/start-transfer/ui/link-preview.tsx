import { platformInfo, PlatformIcon } from "@/entities/platform";
import type { ResolvedLink } from "@/entities/transfer";
import { countOf, TRACKS } from "@/shared/lib/plural";

/** Карточка «что нашли по ссылке»: площадка, название, число треков. */
export function LinkPreview({ link }: { link: ResolvedLink }) {
  const info = platformInfo(link.platform);
  const isLibrary = link.kind === "library";
  const title = link.title ?? (isLibrary ? "Моя медиатека" : "Плейлист");
  const details = [
    info.name,
    isLibrary ? "медиатека" : "плейлист",
    link.track_count != null ? countOf(link.track_count, TRACKS) : null,
  ].filter(Boolean);

  return (
    <div className="flex items-center gap-3 px-4 py-3">
      <PlatformIcon platform={link.platform} className="size-12 rounded-[10px] text-[15px]" />
      <div className="min-w-0">
        <div className="truncate text-[17px] font-semibold">{title}</div>
        <div className="truncate text-[13px] text-label-secondary">{details.join(" · ")}</div>
        {!link.title && !isLibrary && (
          <div className="text-[13px] text-label-tertiary">
            Название и число треков видны, когда {info.name} подключена
          </div>
        )}
      </div>
    </div>
  );
}
