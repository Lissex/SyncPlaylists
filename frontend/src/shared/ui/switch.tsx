import { cn } from "@/shared/lib/cn";

/** Переключатель в духе iOS поверх нативного checkbox (role="switch"): доступен с клавиатуры. */
export function Switch({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
  disabled?: boolean;
}) {
  return (
    <span className="relative inline-flex h-[31px] w-[51px] shrink-0">
      <input
        type="checkbox"
        role="switch"
        aria-label={label}
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        className="peer absolute inset-0 cursor-pointer appearance-none rounded-full opacity-0"
      />
      <span
        aria-hidden="true"
        className={cn(
          "pointer-events-none h-full w-full rounded-full transition-colors",
          "peer-focus-visible:ring-2 peer-focus-visible:ring-brand peer-focus-visible:ring-offset-2",
          checked ? "bg-success" : "bg-label-tertiary/40",
          disabled && "opacity-50",
        )}
      />
      <span
        aria-hidden="true"
        className={cn(
          "pointer-events-none absolute top-[2px] left-[2px] size-[27px] rounded-full bg-white shadow transition-transform",
          checked && "translate-x-5",
        )}
      />
    </span>
  );
}
