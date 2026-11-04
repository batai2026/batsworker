import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SecuritySection } from "./SecuritySection";
import type { SecurityFinding } from "../api";
import {
  addEgressDomain,
  addSecurityFinding,
  getEgressPolicy,
  getOpenShellStatus,
  getSandboxSettings,
  getSecurityFindings,
  getThreatModel,
  removeEgressDomain,
  saveThreatModel,
  setSandboxSettings,
} from "../api";

vi.mock("../api", () => ({
  getThreatModel: vi.fn(),
  saveThreatModel: vi.fn(),
  revealThreatModel: vi.fn(),
  getSecurityFindings: vi.fn(),
  addSecurityFinding: vi.fn(),
  updateSecurityFinding: vi.fn(),
  deleteSecurityFinding: vi.fn(),
  getSandboxSettings: vi.fn(),
  setSandboxSettings: vi.fn(),
  getEgressPolicy: vi.fn(),
  addEgressDomain: vi.fn(),
  removeEgressDomain: vi.fn(),
  getOpenShellStatus: vi.fn(),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function mockEgressDefaults() {
  vi.mocked(getEgressPolicy).mockResolvedValue({ ok: true, domains: [] });
  vi.mocked(getOpenShellStatus).mockResolvedValue({
    ok: true,
    available: false,
    prefix: ["openshell", "exec", "--"],
    backend: "docker",
  });
}

describe("SecuritySection", () => {
  it("loads the threat model and saves edits", async () => {
    vi.mocked(getThreatModel).mockResolvedValue({
      content: "# Threat Model\n\n## In scope\n- Payment paths\n",
      path: "/state/security/threat-model.md",
    });
    vi.mocked(getSecurityFindings).mockResolvedValue([]);
    vi.mocked(getSandboxSettings).mockResolvedValue({
      enabled: false,
      backend: "docker",
      image: "python:3.12-slim",
      network: "none",
    });
    vi.mocked(saveThreatModel).mockResolvedValue({ ok: true });
    mockEgressDefaults();

    render(<SecuritySection />);
    const input = screen.getByTestId("threat-model-input");
    await waitFor(() => expect((input as HTMLTextAreaElement).value).toContain("In scope"));

    fireEvent.change(input, { target: { value: "# Edited\n\n## In scope\n- Only payment paths.\n" } });
    fireEvent.click(screen.getByTestId("threat-model-save"));
    await waitFor(() =>
      expect(saveThreatModel).toHaveBeenCalledWith("# Edited\n\n## In scope\n- Only payment paths.\n"),
    );
    await waitFor(() => expect(screen.getByText("Saved.")).toBeTruthy());
  });

  it("logs a finding and shows it in the list", async () => {
    vi.mocked(getThreatModel).mockResolvedValue({ content: "# TM\n" });
    vi.mocked(getSandboxSettings).mockResolvedValue({
      enabled: false,
      backend: "docker",
      image: "python:3.12-slim",
      network: "none",
    });
    vi.mocked(getSecurityFindings).mockResolvedValue([]);
    const finding: SecurityFinding = {
      id: "F-1",
      title: "SQL injection",
      severity: "high",
      file: "app.py",
      description: "reachable",
      pr_url: "",
      status: "open",
      created_at: "",
      updated_at: "",
    };
    vi.mocked(addSecurityFinding).mockResolvedValue({ ok: true, finding });
    vi.mocked(getSecurityFindings).mockResolvedValue([finding]);
    mockEgressDefaults();

    render(<SecuritySection />);
    await waitFor(() => expect(screen.getByTestId("findings-empty")).toBeTruthy());

    fireEvent.change(screen.getByTestId("finding-title"), { target: { value: "SQL injection" } });
    fireEvent.change(screen.getByTestId("finding-severity"), { target: { value: "high" } });
    fireEvent.click(screen.getByTestId("finding-add"));
    await waitFor(() => expect(screen.getByTestId("finding-F-1")).toBeTruthy());
    expect(screen.getByTestId("finding-F-1").textContent).toContain("SQL injection");
  });

  it("toggles the sandbox and saves settings", async () => {
    vi.mocked(getThreatModel).mockResolvedValue({ content: "# TM\n" });
    vi.mocked(getSecurityFindings).mockResolvedValue([]);
    vi.mocked(getSandboxSettings).mockResolvedValue({
      enabled: false,
      backend: "docker",
      image: "python:3.12-slim",
      network: "none",
    });
    vi.mocked(setSandboxSettings).mockResolvedValue({
      enabled: true,
      backend: "docker",
      image: "python:3.13-slim",
      network: "none",
    });
    mockEgressDefaults();

    render(<SecuritySection />);
    const toggle = screen.getByTestId("sandbox-enabled") as HTMLInputElement;
    await waitFor(() => expect(toggle.checked).toBe(false));

    fireEvent.click(toggle);
    fireEvent.change(screen.getByTestId("sandbox-image"), { target: { value: "python:3.13-slim" } });
    fireEvent.click(screen.getByTestId("sandbox-save"));
    await waitFor(() =>
      expect(setSandboxSettings).toHaveBeenCalledWith({
        enabled: true,
        image: "python:3.13-slim",
        network: "none",
      }),
    );
  });

  it("manages the egress policy allowlist", async () => {
    vi.mocked(getThreatModel).mockResolvedValue({ content: "# TM\n" });
    vi.mocked(getSecurityFindings).mockResolvedValue([]);
    vi.mocked(getSandboxSettings).mockResolvedValue({
      enabled: false,
      backend: "docker",
      image: "python:3.12-slim",
      network: "none",
    });
    vi.mocked(getOpenShellStatus).mockResolvedValue({
      ok: true,
      available: false,
      prefix: ["openshell", "exec", "--"],
      backend: "docker",
    });
    // Mutable mock policy: add/remove update the list the component re-fetches.
    const domains: { domain: string; reason: string }[] = [];
    vi.mocked(getEgressPolicy).mockImplementation(async () => ({ ok: true, domains: [...domains] }));
    vi.mocked(addEgressDomain).mockImplementation(async (domain, reason) => {
      domains.push({ domain, reason: reason || "" });
      return { ok: true };
    });
    vi.mocked(removeEgressDomain).mockImplementation(async (domain) => {
      const i = domains.findIndex((d) => d.domain === domain);
      if (i >= 0) domains.splice(i, 1);
      return { ok: true };
    });

    render(<SecuritySection />);
    await waitFor(() => expect(screen.getByTestId("egress-empty")).toBeTruthy());

    fireEvent.change(screen.getByTestId("egress-domain"), { target: { value: "api.acme.com" } });
    fireEvent.change(screen.getByTestId("egress-reason"), { target: { value: "needed" } });
    fireEvent.click(screen.getByTestId("egress-add"));
    await waitFor(() => expect(addEgressDomain).toHaveBeenCalledWith("api.acme.com", "needed"));

    // The re-fetch shows the added row, then removal empties it.
    const list = await screen.findByTestId("egress-api.acme.com");
    expect(list).toBeTruthy();
    fireEvent.click(screen.getByTestId("egress-remove-api.acme.com"));
    await waitFor(() => expect(removeEgressDomain).toHaveBeenCalledWith("api.acme.com"));
    await waitFor(() => expect(screen.getByTestId("egress-empty")).toBeTruthy());
  });
});