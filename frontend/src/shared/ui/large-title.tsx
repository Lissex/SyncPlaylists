import type { ReactNode } from "react";

/** Крупный заголовок экрана в духе iOS с необязательной подписью. */
export function LargeTitle({ children, subtitle }: { children: ReactNode; subtitle?: ReactNode }) {
  return (
    <header className="space-y-1">
      <h1 className="text-[34px] leading-tight font-bold tracking-tight">{children}</h1>
      {subtitle && <p className="text-[15px] text-label-secondary">{subtitle}</p>}
    </header>
  );
}
