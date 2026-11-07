import { create } from "zustand";
import { getRecentWorkspaces, getSessions, type RecentWorkspace } from "../api";
import type { SessionInfo } from "../types";

// 会话列表 + 最近项目是 GUI 里唯一被多个组件共享、且由服务端推送/轮询刷新的状态。
// 放在 store 里后，非组件代码（事件回调、导航工具）可直接读取，不必再逐层透传 props。
// 其余 App.tsx 的 useState 属于单一视图或生命周期局部状态，继续保持局部（见
// docs/design/gui-state-zustand.md 的切片计划）。

type SessionStore = {
  sessions: SessionInfo[];
  projects: RecentWorkspace[];
  // React 的 Dispatch<SetStateAction<T>> 形状：允许 updater 函数，App.tsx 的 props
  // 类型不需要跟着改。
  setSessions: (next: SessionInfo[] | ((prev: SessionInfo[]) => SessionInfo[])) => void;
  setProjects: (next: RecentWorkspace[] | ((prev: RecentWorkspace[]) => RecentWorkspace[])) => void;
  refreshSessions: () => void;
};

export const useSessionStore = create<SessionStore>((set) => ({
  sessions: [],
  projects: [],
  setSessions: (next) =>
    set((state) => ({
      sessions: typeof next === "function" ? next(state.sessions) : next,
    })),
  setProjects: (next) =>
    set((state) => ({
      projects: typeof next === "function" ? next(state.projects) : next,
    })),
  // Fetch ALL sessions + known projects so the sidebar can group them.
  refreshSessions: () => {
    getSessions()
      .then((rows) => set({ sessions: rows }))
      .catch(() => set({ sessions: [] }));
    getRecentWorkspaces()
      .then((rows) => set({ projects: rows }))
      .catch(() => set({ projects: [] }));
  },
}));
