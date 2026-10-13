import { ClipboardPaste } from "lucide-react";

import type { Account } from "@/entities/account";
import { platformInfo, PlatformIcon } from "@/entities/platform";
import { Button } from "@/shared/ui/button";
import { GroupedList } from "@/shared/ui/grouped-list";
import { Spinner } from "@/shared/ui/spinner";

import { transferErrorMessage } from "../lib/errors";
import type { SourceChoice } from "../model/request";
import { looksLikeLink, type ResolvedLinkState } from "../model/use-resolved-link";
import { LinkPreview } from "./link-preview";

const canPaste = typeof navigator !== "undefined" && Boolean(navigator.clipboard?.readText);

/**
 * Источник: ссылка на плейлист (с предпросмотром) или медиатека одного из подключённых
 * аккаунтов. Сообщает наверх выбор и готовность (ссылка разобрана без ошибки).
 */
export function SourceSection({
  url,
  onUrlChange,
  library,
  onLibraryChange,
  accounts,
  resolved,
}: {
  url: string;
  onUrlChange: (url: string) => void;
  library: Extract<SourceChoice, { kind: "library" }> | null;
  onLibraryChange: (choice: Extract<SourceChoice, { kind: "library" }> | null) => void;
  accounts: readonly Account[];
  resolved: ResolvedLinkState;
}) {
  const typed = url.trim().length > 0;

  const paste = async () => {
    try {
      const text = await navigator.clipboard.readText();
      if (text) {
        onLibraryChange(null);
        onUrlChange(text.trim());
      }
    } catch {
      // Доступ к буферу не дали — вставят вручную.
    }
  };

  return (
    <GroupedList
      header="Откуда"
      footer="Ссылка на плейлист или «Мне нравится» любой площадки. Подойдёт и текст из «Поделиться»."
    >
      <div className="flex items-center gap-2 px-4 py-2">
        <label htmlFor="source-url" className="sr-only">
          Ссылка на плейлист
        </label>
        <input
          id="source-url"
          type="url"
          inputMode="url"
          autoComplete="off"
          spellCheck={false}
          placeholder="https://music.yandex.ru/…"
          value={url}
          onChange={(event) => {
            onLibraryChange(null);
            onUrlChange(event.target.value);
          }}
          className="h-11 min-w-0 flex-1 bg-transparent text-[17px] outline-none placeholder:text-label-tertiary"
        />
        {canPaste && !typed && (
          <Button variant="secondary" size="sm" onClick={paste}>
            <ClipboardPaste className="size-4" aria-hidden="true" />
            Вставить
          </Button>
        )}
        {typed && (
          <Button variant="plain" size="sm" onClick={() => onUrlChange("")} aria-label="Очистить">
            ✕
          </Button>
        )}
      </div>

      {typed && !looksLikeLink(url) && (
        <p className="px-4 py-3 text-[15px] text-label-secondary">Вставьте ссылку целиком.</p>
      )}
      {typed && looksLikeLink(url) && resolved.isFetching && !resolved.data && (
        <div className="flex items-center gap-3 px-4 py-3 text-[15px] text-label-secondary">
          <Spinner /> Смотрим, что по ссылке…
        </div>
      )}
      {typed && resolved.isError && (
        <p role="alert" className="px-4 py-3 text-[15px] text-danger">
          {transferErrorMessage(resolved.error)}
        </p>
      )}
      {typed && resolved.data && !resolved.isError && <LinkPreview link={resolved.data} />}

      {!typed && accounts.length > 0 && (
        <div className="space-y-2 px-4 py-3">
          <div className="text-[13px] text-label-secondary">или перенести медиатеку</div>
          <div className="flex flex-wrap gap-2">
            {accounts.map((account) => {
              const selected = library?.accountId === account.id;
              return (
                <button
                  key={account.id}
                  type="button"
                  aria-pressed={selected}
                  onClick={() =>
                    onLibraryChange(
                      selected
                        ? null
                        : { kind: "library", platform: account.platform, accountId: account.id },
                    )
                  }
                  className={
                    "inline-flex min-h-10 items-center gap-2 rounded-full py-1 pr-3.5 pl-1.5 text-[15px] transition " +
                    (selected ? "bg-brand text-brand-foreground" : "bg-surface-raised text-label")
                  }
                >
                  <PlatformIcon platform={account.platform} className="size-7 rounded-full" />
                  Моя медиатека · {platformInfo(account.platform).name}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </GroupedList>
  );
}
