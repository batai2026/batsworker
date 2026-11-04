import { useState, type Dispatch, type MutableRefObject, type SetStateAction } from "react";
import { createTempWorkspace, getRecentWorkspaces, type RecentWorkspace } from "../api";
import type { Attachment, Item } from "../types";
import type { TFunction } from "../i18n";
import type { PendingPrompt } from "./useSessionActions";

// UX-029 send-time folder enforcement, extracted from App.tsx: a send from a
// code-family session with no folder is stashed in `sendGate`, and the folder
// dialog resolves it by binding a real folder, creating a temporary workspace,
// or cancelling (restoring the draft to the composer).

export interface SendGateDeps {
  model: string;
  setWorkspace: Dispatch<SetStateAction<string | null>>;
  setBranch: Dispatch<SetStateAction<string | null>>;
  setTempWorkspace: Dispatch<SetStateAction<boolean>>;
  setSessionId: Dispatch<SetStateAction<string>>;
  setItems: Dispatch<SetStateAction<Item[]>>;
  setProjects: Dispatch<SetStateAction<RecentWorkspace[]>>;
  pendingPromptRef: MutableRefObject<PendingPrompt | null>;
  newId: () => string;
  prefillComposer: (text: string, attachments?: Attachment[]) => void;
  t: TFunction;
}

export interface SendGateState {
  text: string;
  attachments?: Attachment[];
  skill?: string;
}

export function useSendGate(deps: SendGateDeps) {
  const {
    model,
    setWorkspace,
    setBranch,
    setTempWorkspace,
    setSessionId,
    setItems,
    setProjects,
    pendingPromptRef,
    newId,
    prefillComposer,
    t,
  } = deps;

  const [sendGate, setSendGate] = useState<SendGateState | null>(null);

  // UX-029 send-time dialog resolutions: bind the folder, park the stashed message for
  // the reconnect's onOpen, and let it fly. The user's send already happened — no second
  // click needed.
  const resolveSendFolder = (path: string, b?: string | null) => {
    const gate = sendGate;
    if (!gate) return;
    setSendGate(null);
    setWorkspace(path);
    setBranch(b ?? null);
    setTempWorkspace(false);
    pendingPromptRef.current = { ...gate, model };
    setSessionId(newId());
    getRecentWorkspaces().then(setProjects).catch(() => {});
  };

  const startTempAndSend = async () => {
    const gate = sendGate;
    if (!gate) return;
    const sid = newId();
    const res = await createTempWorkspace(sid, true);
    if (!res.ok || !res.path) {
      setSendGate(null);
      setItems((p) => [
        ...p,
        { kind: "notice", tone: "warn", text: res.error || t("Could not create a temporary folder.") },
      ]);
      prefillComposer(gate.skill ? `/${gate.skill} ${gate.text}` : gate.text, gate.attachments);
      return;
    }
    setSendGate(null);
    setWorkspace(res.path);
    setBranch(null);
    setTempWorkspace(true);
    pendingPromptRef.current = {
      ...gate,
      model,
      notice: res.git ? t("Temporary folder created · git initialized") : t("Temporary folder created"),
    };
    setSessionId(sid);
  };

  const cancelSendGate = () => {
    const gate = sendGate;
    setSendGate(null);
    // Give the draft back — the composer cleared it when the user hit send.
    if (gate) prefillComposer(gate.skill ? `/${gate.skill} ${gate.text}` : gate.text, gate.attachments);
  };

  return { sendGate, setSendGate, resolveSendFolder, startTempAndSend, cancelSendGate };
}