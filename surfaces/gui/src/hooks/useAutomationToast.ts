import { useEffect, useState } from "react";
import { announceAutomationsChanged, connectEvents } from "../api";
import type { TFunction } from "../i18n";

// UX-026: a 5s top-right toast when a SCHEDULED automation run starts (never for
// manual Run-now — the user is already watching). Rides the app-wide /ws/events
// stream; "View run" opens the run's live session. Extracted from App.tsx.

export interface AutomationRunToast {
  title: string;
  sessionId: string;
  workspace: string;
  agent: string;
  time: string;
}

export function useAutomationToast(t: TFunction) {
  const [runToast, setRunToast] = useState<AutomationRunToast | null>(null);

  useEffect(() => {
    const stop = connectEvents((msg) => {
      if (msg.type !== "automation_run_started") return;
      const d = (msg.data ?? {}) as Record<string, string>;
      setRunToast({
        title: d.task_title || t("Automation"),
        sessionId: d.session_id || "",
        workspace: d.workspace || "",
        agent: d.agent || "cowork",
        time: new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }),
      });
      announceAutomationsChanged(); // the Scheduled band's badge is now stale
    });
    return stop;
  }, [t]);

  useEffect(() => {
    if (!runToast) return;
    const timer = window.setTimeout(() => setRunToast(null), 5000);
    return () => window.clearTimeout(timer);
  }, [runToast]);

  return { runToast, setRunToast };
}