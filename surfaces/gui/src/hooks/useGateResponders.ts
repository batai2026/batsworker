import type { RefObject } from "react";
import type { Session } from "../api";
import type { ApprovalDecision, Item } from "../types";
import {
  resolveLastApproval,
  resolveLastDirReq,
  resolveLastItemsReq,
  resolveLastPlan,
  resolveLastQuestion,
  resolveLastTeam,
  resolveLastToolReq,
} from "../resolveGates";

// The live gate-answering flow: optimistically resolve the newest gate card in
// the transcript, drop its parked Inbox mirror, then tell the session socket.
// Extracted from App.tsx (the responders + the bottom-of-file resolveLastX
// helpers moved to `resolveGates.ts`).

export interface GateResponderDeps {
  sessionRef: RefObject<Session | null>;
  setItems: (updater: (prev: Item[]) => Item[]) => void;
  dropSessionInbox: (kind: string) => void;
  setMode: (m: string) => void;
  refreshBoard: () => void;
  setReviewerPaused: (v: boolean) => void;
}

export function useGateResponders(deps: GateResponderDeps) {
  const { sessionRef, setItems, dropSessionInbox, setMode, refreshBoard, setReviewerPaused } = deps;

  const approve = (decision: ApprovalDecision) => {
    setItems((p) => resolveLastApproval(p, decision));
    dropSessionInbox("approval");
    sessionRef.current?.approve(decision);
  };

  const respondPlan = (approved: boolean, mode?: string, feedback?: string) => {
    setItems((p) => resolveLastPlan(p, approved ? "approved" : "rejected"));
    dropSessionInbox("plan");
    sessionRef.current?.respondPlan(approved, mode, feedback);
    if (approved && mode) setMode(mode); // the server flips the live engine to this mode
  };

  const respondTeam = (approved: boolean, feedback?: string, enableChat?: boolean) => {
    setItems((p) => resolveLastTeam(p, approved ? "approved" : "rejected"));
    dropSessionInbox("plan"); // the gate parks as a plan-kind Inbox item
    sessionRef.current?.respondTeam(approved, feedback, enableChat);
  };

  const respondItemsReq = (approved: boolean, feedback?: string) => {
    setItems((p) => resolveLastItemsReq(p, approved ? "approved" : "rejected"));
    dropSessionInbox("plan");
    sessionRef.current?.respondItems(approved, feedback);
    if (approved) setTimeout(refreshBoard, 400); // the items just landed
  };

  const respondDirectory = (granted: boolean, path?: string, writable?: boolean) => {
    setItems((p) => resolveLastDirReq(p, granted ? "granted" : "denied"));
    dropSessionInbox("directory");
    sessionRef.current?.respondDirectory(granted, path, writable);
  };

  const respondTool = (approved: boolean) => {
    setItems((p) => resolveLastToolReq(p, approved ? "installed" : "skipped"));
    dropSessionInbox("tool");
    sessionRef.current?.respondTool(approved);
  };

  const answerQuestion = (answer: string) => {
    setReviewerPaused(false); // an answered question resets the reviewer's streak
    setItems((p) => resolveLastQuestion(p, answer));
    dropSessionInbox("question");
    sessionRef.current?.respondQuestion(answer);
  };

  return { approve, respondPlan, respondTeam, respondItemsReq, respondDirectory, respondTool, answerQuestion };
}