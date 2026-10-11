import { Check } from "lucide-react";

import { formatDuration, type TransferItem } from "@/entities/transfer";
import { cn } from "@/shared/lib/cn";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import { Cover } from "@/shared/ui/cover";

import { durationDelta } from "../lib/duration";

export type Candidate = TransferItem["candidates"][number];

function CandidateBadges({ candidate }: { candidate: Candidate }) {
  return (
    <span className="flex flex-wrap gap-1">
      {candidate.restriction === "preview_only" && <Badge tone="warning">Только превью</Badge>}
      {candidate.rights_holder && <Badge tone="success">Правообладатель</Badge>}
      {candidate.uploader && !candidate.rights_holder && (
        <Badge>Загрузил {candidate.uploader}</Badge>
      )}
    </span>
  );
}

function Meta({
  candidate,
  sourceMs,
}: {
  candidate: Candidate;
  sourceMs: number | null | undefined;
}) {
  const duration = formatDuration(candidate.duration_ms);
  const delta = durationDelta(candidate.duration_ms, sourceMs);
  return (
    <span className="text-[13px] text-label-secondary">
      {duration}
      {delta && (
        <span className={cn("ml-1", delta.large ? "text-warning" : "text-label-tertiary")}>
          ({delta.text})
        </span>
      )}
    </span>
  );
}

type Props = {
  candidate: Candidate;
  sourceMs: number | null | undefined;
  chosen: boolean;
  disabled: boolean;
  onChoose: () => void;
};

/** Кандидат строкой: обложка, название, артист, длительность и разница, пометки. */
export function CandidateRow({ candidate, sourceMs, chosen, disabled, onChoose }: Props) {
  return (
    <div className={cn("flex items-center gap-3 px-4 py-2.5", chosen && "bg-brand/10")}>
      <Cover src={candidate.cover_url} alt="" className="size-12" />
      <div className="min-w-0 flex-1 space-y-0.5">
        <div className="truncate text-[15px] font-medium">{candidate.title}</div>
        <div className="truncate text-[13px] text-label-secondary">
          {candidate.artists.length ? candidate.artists.join(", ") : candidate.artist}
        </div>
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <Meta candidate={candidate} sourceMs={sourceMs} />
          <CandidateBadges candidate={candidate} />
        </div>
      </div>
      {chosen ? (
        <span className="inline-flex items-center gap-1 text-[15px] font-semibold text-brand">
          <Check className="size-4" aria-hidden="true" /> Выбран
        </span>
      ) : (
        <Button variant="secondary" size="sm" disabled={disabled} onClick={onChoose}>
          Выбрать
        </Button>
      )}
    </div>
  );
}

/** Кандидат карточкой для карусели: крупная обложка и всё под ней. */
export function CandidateCard({ candidate, sourceMs, chosen, disabled, onChoose }: Props) {
  return (
    <div
      className={cn(
        "flex w-[min(15rem,72vw)] shrink-0 snap-center flex-col gap-2 rounded-group p-2",
        chosen ? "bg-brand/15 ring-2 ring-brand" : "bg-surface-raised/60",
      )}
    >
      <Cover src={candidate.cover_url} alt="" className="aspect-square w-full rounded-[10px]" />
      <div className="min-w-0 space-y-0.5 px-1">
        <div className="truncate text-[15px] font-semibold">{candidate.title}</div>
        <div className="truncate text-[13px] text-label-secondary">
          {candidate.artists.length ? candidate.artists.join(", ") : candidate.artist}
        </div>
        <Meta candidate={candidate} sourceMs={sourceMs} />
        <CandidateBadges candidate={candidate} />
      </div>
      <Button
        variant={chosen ? "plain" : "primary"}
        size="sm"
        className="mt-auto"
        disabled={disabled || chosen}
        onClick={onChoose}
      >
        {chosen ? "Выбран" : "Выбрать"}
      </Button>
    </div>
  );
}
