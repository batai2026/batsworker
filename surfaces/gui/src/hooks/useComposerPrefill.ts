import { useCallback, useState } from "react";
import type { Attachment } from "../types";

// A pending composer prefill (text + attachments) pushed from the session start
// panel / send-gate recovery. Nonce bumps so repeated prefills re-trigger the
// composer's effect. Extracted from App.tsx.

export interface ComposerPrefill {
  text: string;
  attachments?: Attachment[];
  nonce: number;
}

export function useComposerPrefill() {
  const [composerPrefill, setComposerPrefill] = useState<ComposerPrefill>();

  const prefillComposer = useCallback((text: string, attachments?: Attachment[]) => {
    setComposerPrefill((p) => ({ text, attachments, nonce: (p?.nonce ?? 0) + 1 }));
  }, []);

  return { composerPrefill, prefillComposer };
}