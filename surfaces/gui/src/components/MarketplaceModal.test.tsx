import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MarketplaceModal } from "./MarketplaceModal";
import {
  addMcpServer,
  getConnectorMarketplace,
  installConnectorPlugin,
  uninstallConnectorPlugin,
} from "../api";

vi.mock("../api", () => ({
  addMcpServer: vi.fn(),
  getConnectorMarketplace: vi.fn(),
  installConnectorPlugin: vi.fn(),
  uninstallConnectorPlugin: vi.fn(),
}));

// Mutable marketplace state so install → refresh flips the badge in the mock.
const PLUGINS = [
  { name: "intercom", title: "Intercom", icon: "☎", blurb: "Support search", brand_color: "#1f8ded", logo: "intercom", aliases: [], category: "Support", auth: "token", fields: [], instructions: [], risk_notice: "", installed: false },
];
const MCP = [
  { name: "granola", label: "Granola", blurb: "Meeting notes", config: { type: "http", url: "https://mcp.granola.ai/mcp", auth: "oauth" } },
];

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("MarketplaceModal", () => {
  it("adds an MCP server and installs/uninstalls a plugin", async () => {
    // Mutable marketplace state: install flips `installed`, and refresh() returns
    // the updated list so the Uninstall button appears.
    const plugins = [{ ...PLUGINS[0] }];
    vi.mocked(getConnectorMarketplace).mockResolvedValue({ mcp: MCP, plugins });
    vi.mocked(addMcpServer).mockResolvedValue({ ok: true });
    vi.mocked(installConnectorPlugin).mockImplementation(async () => {
      plugins[0] = { ...plugins[0], installed: true };
      return { ok: true, plugin: plugins[0] } as any;
    });
    vi.mocked(uninstallConnectorPlugin).mockImplementation(async () => {
      plugins[0] = { ...plugins[0], installed: false };
      return { ok: true } as any;
    });
    const onChanged = vi.fn();
    render(<MarketplaceModal onClose={vi.fn()} onChanged={onChanged} />);

    await waitFor(() => expect(screen.getByTestId("marketplace-add-granola")).toBeTruthy());
    fireEvent.click(screen.getByTestId("marketplace-add-granola"));
    await waitFor(() => expect(addMcpServer).toHaveBeenCalledWith("granola", MCP[0].config));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("button", { name: "Community plugins" }));
    await waitFor(() => expect(screen.getByTestId("marketplace-install-intercom")).toBeTruthy());
    fireEvent.click(screen.getByTestId("marketplace-install-intercom"));
    await waitFor(() => expect(installConnectorPlugin).toHaveBeenCalledWith("intercom"));
    await waitFor(() => expect(screen.getByTestId("marketplace-uninstall-intercom")).toBeTruthy());

    fireEvent.click(screen.getByTestId("marketplace-uninstall-intercom"));
    await waitFor(() => expect(uninstallConnectorPlugin).toHaveBeenCalledWith("intercom"));
    await waitFor(() => expect(screen.getByTestId("marketplace-install-intercom")).toBeTruthy());
  });

  it("closes via the ✕ button", async () => {
    vi.mocked(getConnectorMarketplace).mockResolvedValue({ mcp: MCP, plugins: PLUGINS });
    const onClose = vi.fn();
    render(<MarketplaceModal onClose={onClose} onChanged={vi.fn()} />);
    await waitFor(() => expect(screen.getByTestId("marketplace-modal")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalled();
  });
});