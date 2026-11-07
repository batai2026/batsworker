import { useRef } from "react";
import { useRunStore } from "../stores/runStore";

// streaming / reasoningStream 的 ref 镜像：WS 事件回调闭包每个 socket 只建一次，
// 读不到最新的 state —— interrupted/error 的 partial flush 必须在事件到达时读到
// 实时缓冲。镜像因此留在 hook 里（不进 store，避免热路径重渲染），渲染值由
// runStore 持有，这里的 setter 包装同时写镜像与 store。

export interface SessionStream {
  streamingRef: { current: string };
  reasoningRef: { current: string };
  setStreaming: (value: string | ((s: string) => string)) => void;
  setReasoningStream: (value: string) => void;
}

export function useSessionStream(): SessionStream {
  const setStreamingInStore = useRunStore((s) => s.setStreaming);
  const setReasoningStreamInStore = useRunStore((s) => s.setReasoningStream);
  const streamingRef = useRef("");
  const reasoningRef = useRef("");

  const setStreaming = (value: string | ((s: string) => string)) => {
    streamingRef.current = typeof value === "function" ? value(streamingRef.current) : value;
    setStreamingInStore(streamingRef.current);
  };
  const setReasoningStream = (value: string) => {
    reasoningRef.current = value;
    setReasoningStreamInStore(value);
  };

  return { streamingRef, reasoningRef, setStreaming, setReasoningStream };
}
