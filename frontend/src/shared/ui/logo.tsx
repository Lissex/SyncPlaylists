import { cn } from "@/shared/lib/cn";

/** Знак SyncPlaylists: две встречные дуги-стрелки (синхронизация) вокруг ноты. */
export function LogoMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" aria-hidden="true" className={cn("size-8", className)}>
      <rect width="32" height="32" rx="8" className="fill-brand" />
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
        className="text-brand-foreground"
      >
        <path d="M8.5 13.5a8 8 0 0 1 13.6-4.1" />
        <path d="M22.6 6.6v3.3h-3.3" />
        <path d="M23.5 18.5a8 8 0 0 1-13.6 4.1" />
        <path d="M9.4 25.4v-3.3h3.3" />
        <path d="M17.5 12v7.2" />
        <circle cx="15.6" cy="19.4" r="1.9" fill="currentColor" stroke="none" />
      </g>
    </svg>
  );
}

export function Logo({ className }: { className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-2", className)}>
      <LogoMark />
      <span className="text-[17px] font-semibold tracking-tight">SyncPlaylists</span>
    </span>
  );
}
