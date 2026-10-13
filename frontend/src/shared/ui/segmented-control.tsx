import { cn } from "@/shared/lib/cn";

/** Сегментированный переключатель iOS (радиогруппа). */
export function SegmentedControl<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: readonly { value: T; label: string }[];
  onChange: (value: T) => void;
}) {
  return (
    <div
      role="radiogroup"
      aria-label={label}
      className="flex rounded-[9px] bg-surface-raised p-0.5"
    >
      {options.map((option) => {
        const selected = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={selected}
            onClick={() => onChange(option.value)}
            className={cn(
              "min-h-8 flex-1 rounded-[7px] px-3 text-[13px] font-medium transition duration-150 ease-ios",
              selected ? "bg-surface text-label shadow-sm" : "text-label-secondary",
            )}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
