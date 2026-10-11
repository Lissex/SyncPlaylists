import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { accountsQuery } from "@/entities/account";
import { platformInfo } from "@/entities/platform";
import { transferKeys, type Transfer } from "@/entities/transfer";
import { api } from "@/shared/api";
import { useIsTouchDevice } from "@/shared/lib/use-media-query";
import { Button } from "@/shared/ui/button";
import { Spinner } from "@/shared/ui/spinner";

import { transferErrorMessage } from "../lib/errors";
import { buildStartRequest, type DestinationChoice, type SourceChoice } from "../model/request";
import { useResolvedLink } from "../model/use-resolved-link";
import { DestinationSection, existingPlaylistProblem } from "./destination-section";
import { SourceSection } from "./source-section";

/** Новый перенос: источник → предпросмотр → назначение → старт. */
export function StartTransferForm({ onStarted }: { onStarted: (transferId: string) => void }) {
  const queryClient = useQueryClient();
  const touch = useIsTouchDevice();
  const accounts = useQuery(accountsQuery);
  const active = (accounts.data ?? []).filter((a) => a.status === "active");

  const [url, setUrl] = useState("");
  const [library, setLibrary] = useState<Extract<SourceChoice, { kind: "library" }> | null>(null);
  const [destination, setDestination] = useState<DestinationChoice | null>(null);
  const [titleTouched, setTitleTouched] = useState(false);

  const source = useResolvedLink(library ? "" : url);
  const existing = useResolvedLink(destination?.kind === "existing" ? destination.url : "");

  // Название нового плейлиста по умолчанию — как у источника (пока его не правили руками).
  const sourceTitle = library
    ? `Медиатека ${platformInfo(library.platform).name}`
    : (source.data?.title ?? null);
  const effective: DestinationChoice | null =
    destination && !titleTouched && sourceTitle
      ? { ...destination, title: sourceTitle }
      : destination;

  const sourceChoice: SourceChoice | null = library
    ? library
    : source.ready
      ? { kind: "link", url }
      : null;
  const destinationReady =
    effective !== null &&
    (effective.kind !== "existing" ||
      (existing.ready && !existingPlaylistProblem(existing.data, effective.platform)));
  const request = destinationReady ? buildStartRequest(sourceChoice, effective) : null;

  const start = useMutation({
    mutationFn: () => api.post<Transfer>("/transfers", request),
    onSuccess: (transfer) => {
      void queryClient.invalidateQueries({ queryKey: transferKeys.history });
      onStarted(transfer.id);
    },
  });

  if (accounts.isPending) {
    return (
      <div className="flex justify-center py-10">
        <Spinner />
      </div>
    );
  }

  return (
    <form
      className="space-y-8"
      onSubmit={(event) => {
        event.preventDefault();
        if (request) start.mutate();
      }}
    >
      <SourceSection
        url={url}
        onUrlChange={setUrl}
        library={library}
        onLibraryChange={setLibrary}
        accounts={active}
        resolved={source}
      />
      <DestinationSection
        accounts={active}
        value={effective}
        onChange={(next) => {
          // Поле названия правят руками — дальше оно не следует за названием источника.
          if (effective && next.title !== effective.title) setTitleTouched(true);
          setDestination(next);
        }}
        existing={existing}
      />

      <div className="space-y-3">
        {start.isError && (
          <p role="alert" className="text-[15px] text-danger">
            {transferErrorMessage(start.error, destination?.platform)}
          </p>
        )}
        <Button type="submit" size="block" disabled={!request || start.isPending}>
          {start.isPending ? (
            <Spinner className="text-brand-foreground" label="Запускаем" />
          ) : (
            "Перенести"
          )}
        </Button>
        {touch && (
          <p className="text-center text-[13px] text-label-secondary">
            Перенос идёт на сервере и в браузере с расширением на компьютере — эту страницу можно
            закрыть.
          </p>
        )}
      </div>
    </form>
  );
}
