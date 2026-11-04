import { useMemo } from "react";
import type { Item } from "../types";

// The active session's unresolved gate items, derived from the transcript in
// reverse (newest first — a stale gate must not block a newer one). These drive
// the inline live cards above the composer. Extracted from App.tsx; pure
// derivation, no state. Each field keeps the narrowed gate type (same as the
// original inline `.find` inference).

type ApprovalItem = Extract<Item, { kind: "approval" }>;
type DirReqItem = Extract<Item, { kind: "dirreq" }>;
type ToolReqItem = Extract<Item, { kind: "toolreq" }>;
type PlanReqItem = Extract<Item, { kind: "planreq" }>;
type TeamReqItem = Extract<Item, { kind: "teamreq" }>;
type ItemsReqItem = Extract<Item, { kind: "itemsreq" }>;
type QuestionItem = Extract<Item, { kind: "question" }>;

export interface PendingGates {
  pendingApproval: ApprovalItem | undefined;
  pendingDirReq: DirReqItem | undefined;
  pendingToolReq: ToolReqItem | undefined;
  pendingPlan: PlanReqItem | undefined;
  pendingTeam: TeamReqItem | undefined;
  pendingItemsReq: ItemsReqItem | undefined;
  pendingQuestion: QuestionItem | undefined;
}

export function usePendingGates(items: Item[]): PendingGates {
  return useMemo(() => {
    const reversed = items.slice().reverse();
    return {
      pendingApproval: reversed.find((i): i is ApprovalItem => i.kind === "approval" && !i.resolved),
      pendingDirReq: reversed.find((i): i is DirReqItem => i.kind === "dirreq" && !i.resolved),
      pendingToolReq: reversed.find((i): i is ToolReqItem => i.kind === "toolreq" && !i.resolved),
      pendingPlan: reversed.find((i): i is PlanReqItem => i.kind === "planreq" && !i.resolved),
      pendingTeam: reversed.find((i): i is TeamReqItem => i.kind === "teamreq" && !i.resolved),
      pendingItemsReq: reversed.find((i): i is ItemsReqItem => i.kind === "itemsreq" && !i.resolved),
      pendingQuestion: reversed.find((i): i is QuestionItem => i.kind === "question" && !i.resolved),
    };
  }, [items]);
}