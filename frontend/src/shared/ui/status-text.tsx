import type { ReactNode } from "react";

import { cn } from "@/shared/lib/cn";

export type Tone = "ok" | "warning" | "danger" | "muted";

const TONE: Record<Tone, string> = {
  ok: "text-success",
  warning: "text-warning",
  danger: "text-danger",
  muted: "text-label-secondary",
};

/** Статус справа в строке: цветная точка + текст. */
export function StatusText({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-[15px]", TONE[tone])}>
      <span aria-hidden="true" className="size-2 rounded-full bg-current" />
      {children}
    </span>
  );
}
