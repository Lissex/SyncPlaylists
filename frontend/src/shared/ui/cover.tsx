import { Music } from "lucide-react";
import { useState } from "react";

import { cn } from "@/shared/lib/cn";

/**
 * Обложка трека. Нет картинки или она не загрузилась — спокойная заглушка с нотой
 * (обложки источника появятся на этапе enrichment).
 */
export function Cover({
  src,
  alt,
  className,
}: {
  src?: string | null;
  alt: string;
  className?: string;
}) {
  const [failed, setFailed] = useState(false);
  const base = cn("shrink-0 overflow-hidden rounded-[8px] bg-surface-raised", className);
  if (!src || failed) {
    return (
      <span
        role="img"
        aria-label={alt}
        className={cn(base, "inline-flex items-center justify-center")}
      >
        <Music className="size-1/2 text-label-tertiary" aria-hidden="true" />
      </span>
    );
  }
  return (
    <img
      src={src}
      alt={alt}
      loading="lazy"
      referrerPolicy="no-referrer"
      onError={() => setFailed(true)}
      className={cn(base, "object-cover")}
    />
  );
}
