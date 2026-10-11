import { Link } from "@tanstack/react-router";
import { ArrowLeftRight, History, Plug, Settings, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { Logo } from "@/shared/ui/logo";

type NavItem = { to: string; label: string; icon: LucideIcon; exact?: boolean };

const NAV: readonly NavItem[] = [
  { to: "/", label: "Перенос", icon: ArrowLeftRight, exact: true },
  { to: "/transfers", label: "История", icon: History },
  { to: "/connections", label: "Подключения", icon: Plug },
  { to: "/settings", label: "Настройки", icon: Settings },
];

/**
 * Оболочка приложения: на десктопе — верхняя панель с навигацией, на телефоне —
 * нижний таб-бар (как в iOS) с учётом safe area.
 */
export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-dvh">
      <header className="sticky top-0 z-20 hidden border-b-[0.5px] border-separator bg-background/80 backdrop-blur-xl md:block">
        <div className="mx-auto flex h-14 max-w-3xl items-center justify-between px-6">
          <Link to="/" aria-label="SyncPlaylists — на главную">
            <Logo />
          </Link>
          <nav aria-label="Разделы" className="flex items-center gap-1">
            {NAV.map(({ to, label, exact }) => (
              <Link
                key={to}
                to={to}
                activeOptions={{ exact }}
                className="rounded-full px-3.5 py-1.5 text-[15px] text-label-secondary transition hover:text-label"
                activeProps={{ className: "bg-surface-raised text-label!" }}
              >
                {label}
              </Link>
            ))}
          </nav>
        </div>
      </header>

      <main className="mx-auto max-w-3xl px-4 pt-[max(env(safe-area-inset-top),1.25rem)] pb-[calc(env(safe-area-inset-bottom)+6rem)] md:px-6 md:pt-8 md:pb-16">
        {children}
      </main>

      <nav
        aria-label="Разделы"
        className="fixed inset-x-0 bottom-0 z-20 border-t-[0.5px] border-separator bg-background/85 pb-[env(safe-area-inset-bottom)] backdrop-blur-xl md:hidden"
      >
        <ul className="mx-auto flex max-w-md">
          {NAV.map(({ to, label, icon: Icon, exact }) => (
            <li key={to} className="flex-1">
              <Link
                to={to}
                activeOptions={{ exact }}
                className="flex min-h-12 flex-col items-center justify-center gap-0.5 pt-1.5 text-[10px] font-medium text-label-tertiary"
                activeProps={{ className: "text-brand!", "aria-current": "page" }}
              >
                <Icon className="size-6" strokeWidth={1.8} aria-hidden="true" />
                {label}
              </Link>
            </li>
          ))}
        </ul>
      </nav>
    </div>
  );
}

/** Экран без навигации (вход, регистрация) — по центру, ширина формы. */
export function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <main className="mx-auto flex min-h-dvh max-w-sm flex-col justify-center gap-8 px-4 py-[max(env(safe-area-inset-top),2rem)]">
      <Logo className="self-center" />
      {children}
    </main>
  );
}
