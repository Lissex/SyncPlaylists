import { cva, type VariantProps } from "class-variance-authority";
import type { ButtonHTMLAttributes } from "react";

import { cn } from "@/shared/lib/cn";

// Кнопки-таблетки в духе iOS: основная — акцент бренда, вторичная — на сером фоне,
// «plain» — текст цвета бренда без фона (как кнопки в навбаре iOS).
const buttonVariants = cva(
  "inline-flex min-h-11 items-center justify-center gap-2 rounded-full px-5 text-[15px] font-semibold transition duration-150 ease-ios select-none active:scale-[0.98] disabled:pointer-events-none disabled:opacity-40 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand",
  {
    variants: {
      variant: {
        primary: "bg-brand text-brand-foreground hover:brightness-110",
        secondary: "bg-surface-raised text-label hover:brightness-110",
        plain: "min-h-0 px-0 text-brand hover:opacity-80",
        destructive: "bg-surface-raised text-danger hover:brightness-110",
      },
      size: {
        md: "",
        sm: "min-h-8 px-3.5 text-[13px]",
        block: "w-full",
      },
    },
    defaultVariants: { variant: "primary", size: "md" },
  },
);

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> &
  VariantProps<typeof buttonVariants>;

export function Button({ className, variant, size, type = "button", ...props }: ButtonProps) {
  return (
    <button type={type} className={cn(buttonVariants({ variant, size }), className)} {...props} />
  );
}
