import { useCallback, useEffect, useState } from "react";
import { getPersonas, PERSONAS_CHANGED, type Persona } from "../api";

// Persona metadata (extracted from App.tsx). Loads once on mount and refetches on
// PERSONAS_CHANGED (enable/install from Settings) so the composer picker and
// workspace-gating stay fresh. Also exposed for the boot path that calls
// `loadPersonas()` right after health resolves.

export function usePersonas() {
  const [personas, setPersonas] = useState<Persona[] | null>(null);
  const loadPersonas = useCallback(() => {
    getPersonas().then(setPersonas).catch(() => {});
  }, []);
  useEffect(() => {
    loadPersonas();
    // The composer's coworker picker is always mounted on a fresh session — refetch
    // on mutations (enable/install from Settings) instead of going stale.
    window.addEventListener(PERSONAS_CHANGED, loadPersonas);
    return () => window.removeEventListener(PERSONAS_CHANGED, loadPersonas);
  }, [loadPersonas]);

  // Persona metadata drives workspace behavior by FAMILY, not by hardcoded id.
  const personaOf = useCallback((a: string) => personas?.find((p) => p.id === a), [personas]);

  return { personas, loadPersonas, personaOf };
}