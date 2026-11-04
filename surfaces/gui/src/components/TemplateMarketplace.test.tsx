import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { TemplateMarketplace } from "./TemplateMarketplace";
import {
  getAutomationTemplates,
  getConnectors,
  instantiateAutomationTemplate,
} from "../api";

vi.mock("../api", () => ({
  getAutomationTemplates: vi.fn(),
  getConnectors: vi.fn(),
  instantiateAutomationTemplate: vi.fn(),
}));

const TEMPLATES = [
  {
    key: "github",
    title: "GitHub digest",
    blurb: "Merged PRs and commits, posted to Slack.",
    cadence: "Weekly",
    category: "Engineering",
    conns: [{ name: "slack", why: "Where the digest posts" }],
    needs_repo: true,
    needs_channel: true,
    consent: true,
    deliver: false,
    day: "mon",
    time: "09:00",
  },
  {
    key: "news",
    title: "Morning news briefing",
    blurb: "A 5-bullet tech & world news digest.",
    cadence: "Daily",
    category: "Personal",
    conns: [],
    needs_repo: false,
    needs_channel: false,
    consent: false,
    deliver: false,
    day: "daily",
    time: "08:00",
  },
];

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("TemplateMarketplace", () => {
  it("renders the catalog and gates a channel template on its required fields", async () => {
    vi.mocked(getAutomationTemplates).mockResolvedValue(TEMPLATES);
    vi.mocked(getConnectors).mockResolvedValue([]);
    vi.mocked(instantiateAutomationTemplate).mockResolvedValue({ ok: true, task: { id: "t1" } } as any);
    const onCreated = vi.fn();
    render(<TemplateMarketplace onCreated={onCreated} />);

    await waitFor(() =>
      expect(screen.getByTestId("marketplace-template-github")).toBeTruthy(),
    );

    // A no-connector template is immediately creatable.
    fireEvent.click(screen.getByTestId("marketplace-template-news"));
    expect((screen.getByTestId("tmpl-create") as HTMLButtonElement).disabled).toBe(false);

    // The GitHub digest needs repo + channel.
    fireEvent.click(screen.getByTestId("marketplace-template-github"));
    expect((screen.getByTestId("tmpl-create") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByTestId("tmpl-repo"), { target: { value: "acme/worker" } });
    fireEvent.change(screen.getByTestId("tmpl-channel"), { target: { value: "#dev" } });
    expect((screen.getByTestId("tmpl-create") as HTMLButtonElement).disabled).toBe(false);

    fireEvent.click(screen.getByTestId("tmpl-create"));
    await waitFor(() =>
      expect(instantiateAutomationTemplate).toHaveBeenCalledWith(
        "github",
        expect.objectContaining({ repo: "acme/worker", channel: "#dev" }),
      ),
    );
    await waitFor(() => expect(onCreated).toHaveBeenCalled());
  });

  it("search narrows the grid", async () => {
    vi.mocked(getAutomationTemplates).mockResolvedValue(TEMPLATES);
    vi.mocked(getConnectors).mockResolvedValue([]);
    render(<TemplateMarketplace onCreated={vi.fn()} />);
    await waitFor(() =>
      expect(screen.getByTestId("marketplace-template-news")).toBeTruthy(),
    );

    fireEvent.change(screen.getByTestId("tmpl-search"), { target: { value: "github" } });
    expect(screen.getByTestId("marketplace-template-github")).toBeTruthy();
    expect(screen.queryByTestId("marketplace-template-news")).toBeNull();
  });
});