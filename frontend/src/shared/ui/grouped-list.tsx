import type { ReactNode } from "react";

import { cn } from "@/shared/lib/cn";

/** Inset-группа в духе iOS: подпись сверху, строки с разделителями, сноска снизу. */
export function GroupedList({
  header,
  footer,
  children,
  className,
}: {
  header?: ReactNode;
  footer?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cn("space-y-1.5", className)}>
      {header && <h2 className="px-4 text-[13px] text-label-secondary uppercase">{header}</h2>}
      <div className="divide-y-[0.5px] divide-separator overflow-hidden rounded-group bg-surface">
        {children}
      </div>
      {footer && <p className="px-4 text-[13px] text-label-secondary">{footer}</p>}
    </section>
  );
}

export function GroupedRow({
  icon,
  title,
  subtitle,
  trailing,
  className,
}: {
  icon?: ReactNode;
  title: ReactNode;
  subtitle?: ReactNode;
  trailing?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex min-h-11 items-center gap-3 px-4 py-2.5", className)}>
      {icon}
      <div className="min-w-0 flex-1">
        <div className="truncate text-[17px]">{title}</div>
        {subtitle && <div className="truncate text-[13px] text-label-secondary">{subtitle}</div>}
      </div>
      {trailing && <div className="shrink-0 text-label-secondary">{trailing}</div>}
    </div>
  );
}
