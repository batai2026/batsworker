import { useEffect, useState } from "react";
import { getArtifacts } from "../api";

// Produced-file count for the topbar "Artifacts" affordance (works even when the
// rail is hidden, where the rail itself doesn't fetch). Cowork only; refreshes on
// file writes / turn end (the browserRefreshKey cycle). Extracted from App.tsx.

export function useArtifactCount(
  agent: string,
  surface: string,
  sessionId: string,
  browserRefreshKey: number,
): number {
  const [artifactCount, setArtifactCount] = useState(0);

  useEffect(() => {
    if (agent !== "cowork" || surface !== "session") {
      setArtifactCount(0);
      return;
    }
    getArtifacts(sessionId)
      .then((a) => setArtifactCount(a.length))
      .catch(() => {});
  }, [agent, surface, sessionId, browserRefreshKey]);

  return artifactCount;
}