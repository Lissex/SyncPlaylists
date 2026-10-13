import { useId, type InputHTMLAttributes } from "react";

import { cn } from "@/shared/lib/cn";

type TextFieldProps = InputHTMLAttributes<HTMLInputElement> & {
  label: string;
  /** Ошибка под полем; поле помечается aria-invalid. */
  error?: string | null;
  hint?: string;
};

/** Поле ввода в стиле iOS: подпись над полем, мягкий фон, ошибка под полем. */
export function TextField({ label, error, hint, className, id, ...props }: TextFieldProps) {
  const autoId = useId();
  const inputId = id ?? autoId;
  const noteId = `${inputId}-note`;
  const note = error ?? hint;
  return (
    <div className="space-y-1.5">
      <label htmlFor={inputId} className="block px-1 text-[13px] text-label-secondary">
        {label}
      </label>
      <input
        id={inputId}
        aria-invalid={error ? true : undefined}
        aria-describedby={note ? noteId : undefined}
        className={cn(
          "h-12 w-full rounded-group bg-surface px-4 text-[17px] text-label outline-none placeholder:text-label-tertiary",
          "transition duration-150 ease-ios focus:ring-2 focus:ring-brand",
          error && "ring-2 ring-danger focus:ring-danger",
          className,
        )}
        {...props}
      />
      {note && (
        <p
          id={noteId}
          className={cn("px-1 text-[13px]", error ? "text-danger" : "text-label-secondary")}
        >
          {note}
        </p>
      )}
    </div>
  );
}
