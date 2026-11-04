import { useEffect, useState } from "react";
import {
  addMcpServer,
  getConnectorMarketplace,
  installConnectorPlugin,
  uninstallConnectorPlugin,
  type MarketplaceMcpServer,
  type MarketplacePlugin,
} from "../api";
import { useI18n } from "../i18n";
import { Icon } from "./Icon";

// The connector/plugin marketplace (Claude-Marketplace-style door): browse
// curated MCP servers — the open ecosystem — and install community connector
// plugins. MCP servers land in the user's Custom · MCP group with live tools;
// community plugins register new connector cards (credential management; tool
// surfaces come via MCP or a future plugin SDK, stated honestly in the copy).

const CARD = "rounded-xl2 border border-line bg-panel";
const BTN_ACCENT =
  "text-[13px] px-3 py-2 rounded-lg bg-accent text-white shrink-0 disabled:opacity-40";
const BTN_BORDERED =
  "text-[13px] px-3 py-2 rounded-lg border border-line bg-paper hover:border-lineStrong shrink-0";
const CHIP =
  "text-[11px] px-2 py-0.5 rounded-full border border-line bg-paper text-muted shrink-0";

type Tab = "mcp" | "plugins";

export function MarketplaceModal({
  onClose,
  onChanged,
}: {
  onClose: () => void;
  onChanged: () => void;
}) {
  const { t } = useI18n();
  const [tab, setTab] = useState<Tab>("mcp");
  const [mcp, setMcp] = useState<MarketplaceMcpServer[]>([]);
  const [plugins, setPlugins] = useState<MarketplacePlugin[]>([]);
  const [added, setAdded] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");

  const refresh = () => {
    getConnectorMarketplace()
      .then((body) => {
        setMcp(body.mcp || []);
        setPlugins(body.plugins || []);
      })
      .catch(() => {});
  };
  useEffect(() => {
    refresh();
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const addServer = async (s: MarketplaceMcpServer) => {
    setBusy(s.name);
    setError("");
    try {
      const res = await addMcpServer(s.name, s.config);
      if (res.ok === false) setError(res.error || "Could not add the MCP server.");
      else {
        setAdded((prev) => ({ ...prev, [s.name]: true }));
        onChanged(); // the Connectors page picks it up in Custom · MCP
      }
    } catch {
      setError("Could not add the MCP server.");
    } finally {
      setBusy(null);
    }
  };

  const togglePlugin = async (p: MarketplacePlugin) => {
    setBusy(p.name);
    setError("");
    try {
      const res = p.installed
        ? await uninstallConnectorPlugin(p.name)
        : await installConnectorPlugin(p.name);
      if (res.ok === false) setError(res.error || "Something went wrong.");
      else {
        refresh();
        onChanged();
      }
    } catch {
      setError("Something went wrong.");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="fixed inset-0 z-50" data-testid="marketplace-modal">
      <div className="absolute inset-0 bg-black/30 backdrop-blur-[1px]" onClick={onClose} />
      <div className="absolute left-1/2 top-[8vh] -translate-x-1/2 w-[680px] max-w-[94vw] max-h-[84vh] rounded-xl2 border border-line bg-panel shadow-2xl overflow-hidden flex flex-col">
        <div className="px-5 pt-4 pb-3 border-b border-line flex items-center gap-3 shrink-0">
          <div className="min-w-0 flex-1">
            <div className="text-[14px] font-semibold">Connector marketplace</div>
            <div className="text-[12px] text-muted">
              {t("Extend OpenWorker with MCP servers and community connectors.")}
            </div>
          </div>
          <button
            className="text-faint hover:text-ink shrink-0"
            aria-label="Close"
            onClick={onClose}
          >
            <Icon name="x" size={16} />
          </button>
        </div>

        <div className="flex gap-2 px-5 pt-3 shrink-0">
          {(["mcp", "plugins"] as const).map((k) => (
            <button
              key={k}
              type="button"
              className={
                "text-[12px] px-3 py-1.5 rounded-full border " +
                (tab === k
                  ? "border-accent text-accent bg-accentSoft"
                  : "border-line text-muted hover:border-lineStrong")
              }
              onClick={() => setTab(k)}
            >
              {k === "mcp" ? t("MCP servers") : t("Community plugins")}
            </button>
          ))}
        </div>

        <div className="overflow-y-auto hairline-scroll p-5">
          {error ? (
            <div className="text-[13px] text-red-500 mb-3" role="alert">
              {error}
            </div>
          ) : null}

          {tab === "mcp" ? (
            <div className="space-y-2">
              <div className="text-[12px] text-muted leading-relaxed">
                {t("MCP is the open protocol for agent tools. Adding a server makes its tools available in every session.")}
              </div>
              {mcp.length === 0 ? (
                <div className="text-[13px] text-muted py-3">{t("No MCP servers available yet.")}</div>
              ) : (
                mcp.map((s) => (
                  <div key={s.name} className={CARD + " p-3.5 flex items-center gap-3"}>
                    <span className="w-[34px] h-[34px] rounded-lg bg-paper border border-line flex items-center justify-center text-muted shrink-0">
                      <Icon name="code" size={16} />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block text-[13px] font-medium">{s.label}</span>
                      <span className="block text-[12px] text-muted">{s.blurb}</span>
                    </span>
                    <span className={CHIP}>MCP</span>
                    {added[s.name] ? (
                      <span className="text-[13px] text-ok shrink-0">{t("Added")}</span>
                    ) : (
                      <button
                        className={BTN_ACCENT}
                        disabled={busy === s.name}
                        onClick={() => addServer(s)}
                        data-testid={`marketplace-add-${s.name}`}
                      >
                        {busy === s.name ? t("Adding…") : t("Add")}
                      </button>
                    )}
                  </div>
                ))
              )}
            </div>
          ) : (
            <div className="space-y-2">
              <div className="text-[12px] text-muted leading-relaxed">
                {t("Community plugins add connector cards for credential management. Tool surfaces attach via MCP or a future plugin SDK.")}
              </div>
              {plugins.length === 0 ? (
                <div className="text-[13px] text-muted py-3">{t("No plugins available yet.")}</div>
              ) : (
                plugins.map((p) => (
                  <div key={p.name} className={CARD + " p-3.5 flex items-center gap-3"}>
                    <span
                      className="w-[34px] h-[34px] rounded-lg flex items-center justify-center text-white shrink-0"
                      style={{ backgroundColor: p.brand_color || "#6b7280" }}
                    >
                      {p.icon}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center gap-2">
                        <span className="text-[13px] font-medium">{p.title}</span>
                        <span className={CHIP}>{p.category}</span>
                        {p.installed ? <span className={CHIP}>{t("Installed")}</span> : null}
                      </span>
                      <span className="block text-[12px] text-muted">{p.blurb}</span>
                    </span>
                    {p.installed ? (
                      <button
                        className={BTN_BORDERED}
                        disabled={busy === p.name}
                        onClick={() => togglePlugin(p)}
                        data-testid={`marketplace-uninstall-${p.name}`}
                      >
                        {busy === p.name ? "…" : t("Uninstall")}
                      </button>
                    ) : (
                      <button
                        className={BTN_ACCENT}
                        disabled={busy === p.name}
                        onClick={() => togglePlugin(p)}
                        data-testid={`marketplace-install-${p.name}`}
                      >
                        {busy === p.name ? t("Installing…") : t("Install")}
                      </button>
                    )}
                  </div>
                ))
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}