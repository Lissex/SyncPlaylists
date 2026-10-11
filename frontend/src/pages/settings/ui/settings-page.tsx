import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";

import { useSession } from "@/entities/session";
import { LogoutButtons } from "@/features/logout";
import { GroupedList, GroupedRow } from "@/shared/ui/grouped-list";
import { LargeTitle } from "@/shared/ui/large-title";
import { SegmentedControl } from "@/shared/ui/segmented-control";
import {
  getAccent,
  getThemePreference,
  setAccent,
  setThemePreference,
  type AccentOption,
  type ThemePreference,
} from "@/shared/lib/theme";

const THEMES = [
  { value: "dark", label: "Тёмная" },
  { value: "light", label: "Светлая" },
  { value: "system", label: "Авто" },
] as const;

const ACCENTS = [
  { value: "violet", label: "Ультрафиолет" },
  { value: "teal", label: "Бирюза" },
] as const;

export function SettingsPage() {
  const navigate = useNavigate();
  const session = useSession();
  const [theme, setTheme] = useState<ThemePreference>(getThemePreference);
  const [accent, setAccentState] = useState<AccentOption>(getAccent);

  return (
    <div className="space-y-8">
      <LargeTitle>Настройки</LargeTitle>

      <GroupedList header="Аккаунт">
        <GroupedRow title={session.data?.email ?? "…"} subtitle="Email для входа" />
      </GroupedList>

      <GroupedList header="Оформление">
        <div className="space-y-3 px-4 py-3">
          <div className="text-[15px]">Тема</div>
          <SegmentedControl
            label="Тема"
            value={theme}
            options={THEMES}
            onChange={(value) => {
              setTheme(value);
              setThemePreference(value);
            }}
          />
        </div>
        <div className="space-y-3 px-4 py-3">
          <div className="text-[15px]">Акцент (временно — выбираем фирменный цвет)</div>
          <SegmentedControl
            label="Акцент"
            value={accent}
            options={ACCENTS}
            onChange={(value) => {
              setAccentState(value);
              setAccent(value);
            }}
          />
        </div>
      </GroupedList>

      <LogoutButtons onDone={() => navigate({ to: "/login", replace: true })} />
    </div>
  );
}
