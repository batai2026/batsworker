import { useCallback, useEffect, useState } from "react";

// Navigation state for the full-page surfaces (extracted from App.tsx):
// which surface is showing, which automation/persona/settings section it opens
// on, and where the persona page returns on Back. Pure state moves — behavior
// is identical to the original inline declarations.

export type AppSurface =
  | "session"
  | "scheduled"
  | "integrations"
  | "audit"
  | "inbox"
  | "persona"
  | "settings";

export type SettingsTab =
  | "appearance"
  | "models"
  | "skills"
  | "voice"
  | "memory"
  | "personas";

export function useAppNavigation() {
  // Which automation the Automations surface opens on (set by the banner's Back
  // link or a sidebar Scheduled-band click). Cleared on leaving the surface: a
  // remembered id going stale (e.g. the automation was deleted) reopened a dead
  // detail — "Loading…" forever (owner-hit 2026-07-20). Nav re-entry lands on
  // the list.
  const [scheduledOpenId, setScheduledOpenId] = useState<string | null>(null);
  const [surface, setSurface] = useState<AppSurface>("session");
  useEffect(() => {
    if (surface !== "scheduled") setScheduledOpenId(null);
  }, [surface]);

  // Which Settings section the full-page Settings surface opens on (§ Settings-as-page).
  const [settingsTab, setSettingsTab] = useState<SettingsTab>("appearance");
  const openSettings = useCallback((tab: SettingsTab = "appearance") => {
    setSettingsTab(tab);
    setSurface("settings");
  }, []);

  // The persona whose detail page is showing (surface === "persona"); empty falls
  // back to the active session's persona.
  const [personaViewId, setPersonaViewId] = useState<string>("");
  // Where the persona page returns on "back": the active session, or Settings ▸
  // Personas when it was opened from there (persona config lives in Settings).
  const [personaViewReturn, setPersonaViewReturn] = useState<"session" | "settings">("session");
  const openPersona = useCallback((id: string, from: "session" | "settings" = "session") => {
    setPersonaViewReturn(from);
    setPersonaViewId(id);
    setSurface("persona");
  }, []);

  return {
    scheduledOpenId,
    setScheduledOpenId,
    surface,
    setSurface,
    settingsTab,
    openSettings,
    personaViewId,
    personaViewReturn,
    openPersona,
  };
}