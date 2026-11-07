import { create } from "zustand";

// 活动会话的身份与工作区：sessionId / workspace / branch 是连接与发送路径的输入，
// agent / model 是当前人格与模型。它们被 boot、connect、sendGate、sessionActions
// 四个 hook 共享，websocket 回调等非组件代码也需要随时读取，所以放进 store
// （`useActiveSessionStore.getState()`）。tempWorkspace / draftFolderPicked 等
// 只服务于发送门禁的局部状态仍留在 App.tsx —— 见 docs/design/gui-state-zustand.md。

export function newSessionId(): string {
  return (crypto as any).randomUUID
    ? crypto.randomUUID().slice(0, 12)
    : Math.random().toString(36).slice(2, 14);
}

// React 的 Dispatch<SetStateAction<T>> 形状：允许 updater 函数，现有 hook 的依赖
// 类型（useBoot / useSessionConnect / useSendGate / useSessionActions）无需改动。
type Setter<T> = (next: T | ((prev: T) => T)) => void;

type ActiveSessionStore = {
  sessionId: string;
  workspace: string | null;
  branch: string | null;
  agent: string;
  model: string;
  setSessionId: Setter<string>;
  setWorkspace: Setter<string | null>;
  setBranch: Setter<string | null>;
  setAgent: Setter<string>;
  setModel: Setter<string>;
};

export const useActiveSessionStore = create<ActiveSessionStore>((set) => ({
  sessionId: newSessionId(),
  workspace: null,
  branch: null,
  agent: "cowork",
  model: "gpt-5.6-sol",
  setSessionId: (next) =>
    set((s) => ({ sessionId: typeof next === "function" ? next(s.sessionId) : next })),
  setWorkspace: (next) =>
    set((s) => ({ workspace: typeof next === "function" ? next(s.workspace) : next })),
  setBranch: (next) =>
    set((s) => ({ branch: typeof next === "function" ? next(s.branch) : next })),
  setAgent: (next) => set((s) => ({ agent: typeof next === "function" ? next(s.agent) : next })),
  setModel: (next) => set((s) => ({ model: typeof next === "function" ? next(s.model) : next })),
}));
