import { HistoryList } from "@/widgets/history-list";
import { LargeTitle } from "@/shared/ui/large-title";

export function HistoryPage() {
  return (
    <div className="space-y-8">
      <LargeTitle>История</LargeTitle>
      <HistoryList />
    </div>
  );
}
