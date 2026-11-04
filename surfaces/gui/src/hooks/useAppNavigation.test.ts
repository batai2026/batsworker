import { describe, expect, it } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useAppNavigation } from "./useAppNavigation";

describe("useAppNavigation", () => {
  it("starts on the session surface and opens settings/persona", () => {
    const { result } = renderHook(() => useAppNavigation());

    expect(result.current.surface).toBe("session");

    act(() => result.current.openSettings("models"));
    expect(result.current.surface).toBe("settings");
    expect(result.current.settingsTab).toBe("models");

    act(() => result.current.openPersona("security", "settings"));
    expect(result.current.surface).toBe("persona");
    expect(result.current.personaViewId).toBe("security");
    expect(result.current.personaViewReturn).toBe("settings");
  });

  it("clears scheduledOpenId when leaving the scheduled surface", () => {
    const { result } = renderHook(() => useAppNavigation());

    act(() => {
      result.current.setScheduledOpenId("task-1");
      result.current.setSurface("scheduled");
    });
    expect(result.current.scheduledOpenId).toBe("task-1");

    act(() => result.current.setSurface("session"));
    expect(result.current.scheduledOpenId).toBeNull();
  });

  it("openSettings and openPersona are stable callbacks", () => {
    const { result } = renderHook(() => useAppNavigation());
    const a = result.current.openSettings;
    const p = result.current.openPersona;
    expect(a).toBe(result.current.openSettings);
    expect(p).toBe(result.current.openPersona);
  });
});