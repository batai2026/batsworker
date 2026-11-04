import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, renderHook, waitFor } from "@testing-library/react";
import { usePersonas } from "./usePersonas";
import { getPersonas, PERSONAS_CHANGED } from "../api";

vi.mock("../api", () => ({
  getPersonas: vi.fn(),
  PERSONAS_CHANGED: "coworker:personas-changed",
}));

const PERSONAS = [
  {
    id: "cowork",
    name: "OpenWorker",
    icon: "cowork",
    tagline: "Produce a deliverable",
    requires_folder: false,
    builtin: true,
    enabled: true,
    surfaced: true,
    default: true,
    ships: true,
    group: "general",
  },
] as any;

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("usePersonas", () => {
  it("loads personas on mount and resolves personaOf", async () => {
    vi.mocked(getPersonas).mockResolvedValue(PERSONAS);
    const { result } = renderHook(() => usePersonas());

    await waitFor(() => expect(result.current.personas?.length).toBe(1));
    expect(result.current.personaOf("cowork")?.name).toBe("OpenWorker");
    expect(result.current.personaOf("ghost")).toBeUndefined();
  });

  it("refetches on PERSONAS_CHANGED", async () => {
    vi.mocked(getPersonas).mockResolvedValue([]);
    const { result } = renderHook(() => usePersonas());
    await waitFor(() => expect(result.current.personas).toEqual([]));

    vi.mocked(getPersonas).mockResolvedValue(PERSONAS);
    window.dispatchEvent(new CustomEvent(PERSONAS_CHANGED));
    await waitFor(() => expect(result.current.personas?.length).toBe(1));
  });
});