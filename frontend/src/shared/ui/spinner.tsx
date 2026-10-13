import { cn } from "@/shared/lib/cn";

export function Spinner({ className, label = "Загрузка" }: { className?: string; label?: string }) {
  return (
    <span
      role="status"
      aria-label={label}
      className={cn(
        "inline-block size-5 animate-spin rounded-full border-2 border-current border-t-transparent text-label-secondary",
        className,
      )}
    />
  );
}
