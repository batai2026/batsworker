import { create } from "zustand";
import type { Item, SessionUsage, TodoItem } from "../types";
import { emptyUsage } from "../usage";

// 一次运行的渲染态：这些值被 App.tsx、连接事件的 switch、多个卡片组件共享，
// 且 websocket 回调等非组件代码需要随时读写。ref 镜像（streaming / reasoning）
// 不进 store —— 见 hooks/useSessionStream.ts 与 docs/design/gui-state-zustand.md。

// React 的 Dispatch<SetStateAction<T>> 形状：允许 updater 函数，现有 hook 的依赖
// 类型（useBoot / useSessionConnect / useSessionActions 等）无需改动。
type Setter<T> = (next: T | ((prev: T) => T)) => void;

type RunStore = {
  running: boolean;
  compacting: boolean;
  streaming: string;
  reasoningStream: string;
  items: Item[];
  todo: TodoItem[];
  usage: SessionUsage;
  setRunning: Setter<boolean>;
  setCompacting: Setter<boolean>;
  setStreaming: Setter<string>;
  setReasoningStream: Setter<string>;
  setItems: Setter<Item[]>;
  setTodo: Setter<TodoItem[]>;
  setUsage: Setter<SessionUsage>;
};

export const useRunStore = create<RunStore>((set) => ({
  running: false,
  compacting: false,
  streaming: "",
  reasoningStream: "",
  items: [],
  todo: [],
  usage: emptyUsage(),
  setRunning: (next) => set((s) => ({ running: typeof next === "function" ? next(s.running) : next })),
  setCompacting: (next) =>
    set((s) => ({ compacting: typeof next === "function" ? next(s.compacting) : next })),
  setStreaming: (next) =>
    set((s) => ({ streaming: typeof next === "function" ? next(s.streaming) : next })),
  setReasoningStream: (next) =>
    set((s) => ({ reasoningStream: typeof next === "function" ? next(s.reasoningStream) : next })),
  setItems: (next) => set((s) => ({ items: typeof next === "function" ? next(s.items) : next })),
  setTodo: (next) => set((s) => ({ todo: typeof next === "function" ? next(s.todo) : next })),
  setUsage: (next) => set((s) => ({ usage: typeof next === "function" ? next(s.usage) : next })),
}));
