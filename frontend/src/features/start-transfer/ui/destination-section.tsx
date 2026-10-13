import type { Account } from "@/entities/account";
import { platformInfo, PlatformIcon, type Platform } from "@/entities/platform";
import type { ResolvedLink } from "@/entities/transfer";
import { cn } from "@/shared/lib/cn";
import { GroupedList } from "@/shared/ui/grouped-list";
import { SegmentedControl } from "@/shared/ui/segmented-control";
import { Spinner } from "@/shared/ui/spinner";

import { transferErrorMessage } from "../lib/errors";
import { MAX_TITLE_LENGTH, type DestinationChoice, type DestinationKind } from "../model/request";
import { LinkPreview } from "./link-preview";

const KINDS = [
  { value: "new", label: "Новый плейлист" },
  { value: "existing", label: "Мой плейлист" },
  { value: "library", label: "Медиатека" },
] as const satisfies readonly { value: DestinationKind; label: string }[];

const LIBRARY_NOTE: Partial<Record<string, string>> = {
  vk: "VK разрешает добавлять в «Мою музыку» ограниченное число треков в сутки — большая медиатека перенесётся за несколько дней, сама.",
  yandex: "Треки добавятся в «Мне нравится» в том же порядке, самые свежие — сверху.",
};

/** Результат проверки ссылки на существующий плейлист: подходит ли он как назначение. */
export function existingPlaylistProblem(
  link: ResolvedLink | undefined,
  platform: Platform,
): string | null {
  if (!link) return null;
  if (link.platform !== platform) {
    return `Это ссылка на ${platformInfo(link.platform).name}, а выбрана ${platformInfo(platform).name}.`;
  }
  if (link.kind !== "playlist")
    return "Нужна ссылка на плейлист. Для медиатеки выберите «Медиатека».";
  return null;
}

export function DestinationSection({
  accounts,
  value,
  onChange,
  existing,
}: {
  accounts: readonly Account[];
  value: DestinationChoice | null;
  onChange: (value: DestinationChoice) => void;
  /** Разбор ссылки на существующий плейлист (делает форма). */
  existing: { data?: ResolvedLink; error: unknown; isFetching: boolean };
}) {
  if (accounts.length === 0) {
    return (
      <GroupedList header="Куда">
        <p className="px-4 py-3 text-[15px] text-label-secondary">
          Подключите хотя бы одну площадку в разделе «Подключения» — туда и перенесём.
        </p>
      </GroupedList>
    );
  }

  const choose = (account: Account) =>
    onChange({
      platform: account.platform,
      accountId: account.id,
      kind: value?.kind ?? "new",
      title: value?.title ?? "",
      url: "",
    });

  const problem =
    value?.kind === "existing" ? existingPlaylistProblem(existing.data, value.platform) : null;

  return (
    <GroupedList header="Куда">
      <div className="flex flex-wrap gap-2 px-4 py-3">
        {accounts.map((account) => {
          const selected = value?.accountId === account.id;
          return (
            <button
              key={account.id}
              type="button"
              aria-pressed={selected}
              onClick={() => choose(account)}
              className={cn(
                "inline-flex min-h-10 items-center gap-2 rounded-full py-1 pr-3.5 pl-1.5 text-[15px] transition",
                selected ? "bg-brand text-brand-foreground" : "bg-surface-raised text-label",
              )}
            >
              <PlatformIcon platform={account.platform} className="size-7 rounded-full" />
              {platformInfo(account.platform).name}
            </button>
          );
        })}
      </div>

      {value && (
        <div className="space-y-3 px-4 py-3">
          <SegmentedControl
            label="Куда записать"
            value={value.kind}
            options={KINDS}
            onChange={(kind) => onChange({ ...value, kind })}
          />

          {value.kind === "new" && (
            <div className="space-y-1">
              <label htmlFor="new-title" className="block text-[13px] text-label-secondary">
                Название нового плейлиста
              </label>
              <input
                id="new-title"
                maxLength={MAX_TITLE_LENGTH}
                value={value.title}
                onChange={(event) => onChange({ ...value, title: event.target.value })}
                className="h-11 w-full rounded-[10px] bg-surface-raised px-3 text-[17px] outline-none focus:ring-2 focus:ring-brand"
              />
            </div>
          )}

          {value.kind === "existing" && (
            <div className="space-y-2">
              <label htmlFor="existing-url" className="block text-[13px] text-label-secondary">
                Ссылка на ваш плейлист в {platformInfo(value.platform).name}
              </label>
              <input
                id="existing-url"
                type="url"
                inputMode="url"
                spellCheck={false}
                value={value.url}
                onChange={(event) => onChange({ ...value, url: event.target.value })}
                className="h-11 w-full rounded-[10px] bg-surface-raised px-3 text-[17px] outline-none focus:ring-2 focus:ring-brand"
              />
              {existing.isFetching && !existing.data && <Spinner />}
              {Boolean(existing.error) && (
                <p role="alert" className="text-[15px] text-danger">
                  {transferErrorMessage(existing.error, value.platform)}
                </p>
              )}
              {problem && (
                <p role="alert" className="text-[15px] text-danger">
                  {problem}
                </p>
              )}
              {existing.data && !problem && (
                <div className="-mx-4">
                  <LinkPreview link={existing.data} />
                </div>
              )}
              <p className="text-[13px] text-label-secondary">
                Добавим только новые треки — то, что уже есть в плейлисте, не задвоится.
              </p>
            </div>
          )}

          {value.kind === "library" && (
            <p className="text-[13px] text-label-secondary">
              {LIBRARY_NOTE[value.platform] ??
                "Треки добавятся в вашу медиатеку; то, что уже есть, не задвоится."}
            </p>
          )}
        </div>
      )}
    </GroupedList>
  );
}
