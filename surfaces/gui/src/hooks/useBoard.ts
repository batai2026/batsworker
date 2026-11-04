import { useCallback, useEffect, useState } from "react";
import { boardTransition, getBoard, type Board } from "../api";

// Agent teams (OPE-96): the session's board — drives the rail section, the plan
// gate, and the expanded overlay. Refreshes with the same cycle as artifacts
// (session change + turn end) so items the agent just created appear. Also owns
// the board overlay + team-chat visibility state. Extracted from App.tsx.

export function useBoard(
  agent: string,
  surface: string,
  sessionId: string,
  browserRefreshKey: number,
  running: boolean,
) {
  const [board, setBoard] = useState<Board | null>(null);
  const [boardOpen, setBoardOpen] = useState(false);
  const [boardDetailId, setBoardDetailId] = useState<number | null>(null);
  // # team chat overlay — opened from the team entry's chat row.
  const [chatTeam, setChatTeam] = useState<string | null>(null);

  useEffect(() => {
    if (surface !== "session" || agent === "chat") {
      setBoard(null);
      return;
    }
    getBoard(sessionId).then(setBoard).catch(() => setBoard(null));
  }, [agent, surface, sessionId, browserRefreshKey, running]);

  const refreshBoard = useCallback(() => {
    getBoard(sessionId).then(setBoard).catch(() => {});
  }, [sessionId]);

  const moveBoardItem = useCallback(
    async (item: number, to: string, comment = "") => {
      await boardTransition(sessionId, item, to, comment);
      await refreshBoard();
    },
    [sessionId, refreshBoard],
  );

  return {
    board,
    setBoard,
    boardOpen,
    setBoardOpen,
    boardDetailId,
    setBoardDetailId,
    chatTeam,
    setChatTeam,
    refreshBoard,
    moveBoardItem,
  };
}