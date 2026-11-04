import { useEffect, useState } from "react";
import {
  addEgressDomain,
  addSecurityFinding,
  deleteSecurityFinding,
  getEgressPolicy,
  getOpenShellStatus,
  getSandboxSettings,
  getSecurityFindings,
  getThreatModel,
  removeEgressDomain,
  revealThreatModel,
  saveThreatModel,
  setSandboxSettings,
  updateSecurityFinding,
  type EgressPolicyEntry,
  type OpenShellStatus,
  type SandboxSettings,
  type SecurityFinding,
} from "../api";
import { useI18n } from "../i18n";
import { PanelHead } from "./IntegrationsView";

// Security workflow (Claude-Security style): the editable threat model tells
// security coworkers what to flag vs noise, and the finding tracker carries each
// finding open → fixing → fixed → closed with its fix PR linked.

const CARD = "rounded-xl2 border border-line bg-panel";
const FIELD_LABEL = "text-[13px] font-medium text-ink";
const FIELD_HELP = "text-[12px] text-muted mt-1.5 leading-relaxed";
const INPUT =
  "w-full min-w-0 px-3 py-2 rounded-lg border border-line bg-paper text-[13px] text-ink outline-none focus:border-accent";
const BTN_ACCENT =
  "text-[13px] px-3 py-2 rounded-lg bg-accent text-white shrink-0 disabled:opacity-40";
const BTN_BORDERED =
  "text-[13px] px-3 py-2 rounded-lg border border-line bg-paper hover:border-lineStrong shrink-0";

const SEVERITY_STYLE: Record<string, string> = {
  critical: "bg-dangerSoft text-danger border-danger/30",
  high: "bg-warnSoft text-warnInk border-warnInk/30",
  medium: "bg-accentSoft text-accent border-accent/30",
  low: "bg-paper text-muted border-line",
  noise: "bg-paper text-faint border-line",
};

export function SecuritySection() {
  const { t } = useI18n();
  const [model, setModel] = useState("");
  const [modelPath, setModelPath] = useState("");
  const [savedMsg, setSavedMsg] = useState(false);
  const [findings, setFindings] = useState<SecurityFinding[]>([]);
  const [sandbox, setSandbox] = useState<SandboxSettings>({
    enabled: false,
    backend: "docker",
    image: "python:3.12-slim",
    network: "none",
  });
  const [sandboxSaved, setSandboxSaved] = useState(false);
  // Egress policy (P0): persistent allowlist of network destinations approved via
  // request_egress. The user manages it here; the permission engine auto-allows.
  const [egressDomains, setEgressDomains] = useState<EgressPolicyEntry[]>([]);
  const [egressDomain, setEgressDomain] = useState("");
  const [egressReason, setEgressReason] = useState("");
  const [egressMsg, setEgressMsg] = useState<string | null>(null);
  const [openshell, setOpenShell] = useState<OpenShellStatus | null>(null);
  const [draft, setDraft] = useState({
    title: "",
    severity: "medium",
    file: "",
    description: "",
    pr_url: "",
  });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const refreshFindings = () =>
    getSecurityFindings().then(setFindings).catch(() => setFindings([]));

  useEffect(() => {
    getThreatModel()
      .then((r) => {
        setModel(r.content || "");
        if (r.path) setModelPath(r.path);
      })
      .catch(() => {});
    refreshFindings();
    getSandboxSettings().then(setSandbox).catch(() => {});
    getEgressPolicy()
      .then((r) => setEgressDomains(r.domains || []))
      .catch(() => {});
    getOpenShellStatus().then(setOpenShell).catch(() => {});
  }, []);

  const saveModel = async () => {
    const r = await saveThreatModel(model);
    if (r.ok) {
      setSavedMsg(true);
      window.setTimeout(() => setSavedMsg(false), 3000);
    } else {
      setMsg(r.error || "Could not save the threat model.");
    }
  };

  const reveal = async () => {
    const r = await revealThreatModel();
    if (!r.ok) setMsg(r.error || "Could not open the threat model folder.");
  };

  const saveSandbox = async () => {
    const next = await setSandboxSettings({
      enabled: sandbox.enabled,
      image: sandbox.image,
      network: sandbox.network,
    });
    setSandbox(next);
    setSandboxSaved(true);
    window.setTimeout(() => setSandboxSaved(false), 3000);
  };

  const addFinding = async () => {
    setBusy(true);
    setMsg(null);
    const r = await addSecurityFinding(draft);
    setBusy(false);
    if (!r.ok) {
      setMsg(r.error || "Could not add the finding.");
      return;
    }
    setDraft({ title: "", severity: "medium", file: "", description: "", pr_url: "" });
    refreshFindings();
  };

  const setStatus = async (f: SecurityFinding, status: string) => {
    const r = await updateSecurityFinding(f.id, { status });
    if (r.ok) refreshFindings();
    else setMsg(r.error || "Could not update the finding.");
  };

  const remove = async (f: SecurityFinding) => {
    const r = await deleteSecurityFinding(f.id);
    if (r.ok) refreshFindings();
    else setMsg(r.error || "Could not delete the finding.");
  };

  const addEgress = async () => {
    setEgressMsg(null);
    const domain = egressDomain.trim();
    if (!domain) return;
    const r = await addEgressDomain(domain, egressReason.trim());
    if (!r.ok) {
      setEgressMsg(r.error || "Could not add the domain.");
      return;
    }
    setEgressDomain("");
    setEgressReason("");
    getEgressPolicy()
      .then((res) => setEgressDomains(res.domains || []))
      .catch(() => {});
  };

  const removeEgress = async (domain: string) => {
    await removeEgressDomain(domain);
    getEgressPolicy()
      .then((r) => setEgressDomains(r.domains || []))
      .catch(() => {});
  };

  return (
    <section>
      <PanelHead
        title={t("Security")}
        sub={t("Editable threat model + tracked findings — the loop the Security Coworkers follow.")}
      />

      {/* Threat model */}
      <div className={CARD + " p-4 mb-4"} data-testid="threat-model-card">
        <div className="flex items-center gap-2">
          <div className={FIELD_LABEL + " flex-1"}>{t("Threat model")}</div>
          <button className={BTN_BORDERED} onClick={reveal} data-testid="threat-model-reveal">
            {t("Show folder")}
          </button>
        </div>
        <div className={FIELD_HELP}>
          {t("Security coworkers read this before triaging: what to flag, what is noise, reachability rules, preferred mitigations. Edit like a document — it lives at {path}.", {
            path: modelPath || "…",
          })}
        </div>
        <textarea
          className={INPUT + " mt-2.5 min-h-[180px] font-mono resize-y leading-relaxed"}
          value={model}
          onChange={(e) => setModel(e.target.value)}
          data-testid="threat-model-input"
        />
        <div className="flex items-center gap-3 mt-2">
          <button className={BTN_ACCENT} onClick={saveModel} data-testid="threat-model-save">
            {t("Save threat model")}
          </button>
          {savedMsg && <span className="text-[13px] text-ok">{t("Saved.")}</span>}
        </div>
      </div>

      {/* Sandbox (OS-level): shell commands run inside a Docker container when enabled —
          no network, read-only root. Commands run in the sandbox show a 🛡 badge. */}
      <div className={CARD + " p-4 mb-4"} data-testid="sandbox-card">
        <div className={FIELD_LABEL}>{t("Sandbox")}</div>
        <div className={FIELD_HELP}>
          {t("When enabled, shell commands run in a Docker container with no network and a read-only root filesystem; the workspace is the only writable path.")}
        </div>
        <div className="flex items-center gap-3 mt-3">
          <input
            type="checkbox"
            role="switch"
            aria-label={t("Enable the sandbox")}
            checked={sandbox.enabled}
            onChange={(e) => setSandbox({ ...sandbox, enabled: e.target.checked })}
            data-testid="sandbox-enabled"
          />
          <span className="text-[13px]">{t("Enable sandbox for shell commands")}</span>
        </div>
        <div className="grid grid-cols-2 gap-2 mt-3">
          <label className="block">
            <span className="block text-[12px] text-muted mb-1">{t("Container image")}</span>
            <input
              className={INPUT}
              value={sandbox.image}
              onChange={(e) => setSandbox({ ...sandbox, image: e.target.value })}
              data-testid="sandbox-image"
            />
          </label>
          <label className="block">
            <span className="block text-[12px] text-muted mb-1">{t("Network mode")}</span>
            <input
              className={INPUT}
              value={sandbox.network}
              onChange={(e) => setSandbox({ ...sandbox, network: e.target.value })}
              data-testid="sandbox-network"
            />
          </label>
        </div>
        <div className="flex items-center gap-3 mt-2">
          <button className={BTN_ACCENT} onClick={saveSandbox} data-testid="sandbox-save">
            {t("Save sandbox settings")}
          </button>
          {sandboxSaved && <span className="text-[13px] text-ok">{t("Saved.")}</span>}
        </div>
      </div>

      {/* Egress policy (P0): persistent allowlist of network destinations that
          request_egress adds once the user approves. Approved domains auto-run. */}
      <div className={CARD + " p-4 mb-4"} data-testid="egress-card">
        <div className={FIELD_LABEL}>{t("Egress policy")}</div>
        <div className={FIELD_HELP}>
          {t("When a blocked site is needed, security coworkers call request_egress and wait for approval. Approved domains are listed here and auto-run without asking again.")}
        </div>

        <div className="flex items-center gap-2 mt-3">
          <input
            className={INPUT}
            placeholder={t("Domain, e.g. api.example.com")}
            value={egressDomain}
            onChange={(e) => setEgressDomain(e.target.value)}
            data-testid="egress-domain"
          />
          <input
            className={INPUT + " flex-1"}
            placeholder={t("Reason (optional)")}
            value={egressReason}
            onChange={(e) => setEgressReason(e.target.value)}
            data-testid="egress-reason"
          />
          <button className={BTN_ACCENT} disabled={!egressDomain.trim()} onClick={addEgress} data-testid="egress-add">
            {t("Allow domain")}
          </button>
        </div>
        {egressMsg && <div className="text-[13px] text-red-500 mt-2">{egressMsg}</div>}

        {egressDomains.length === 0 ? (
          <div className="text-[12px] text-muted mt-3" data-testid="egress-empty">
            {t("No approved egress domains yet.")}
          </div>
        ) : (
          <div className="mt-3 divide-y divide-line" data-testid="egress-list">
            {egressDomains.map((entry) => (
              <div key={entry.domain} className="py-2 flex items-start gap-3" data-testid={`egress-${entry.domain}`}>
                <div className="min-w-0 flex-1">
                  <div className="text-[13px] font-medium font-mono">{entry.domain}</div>
                  {entry.reason && <div className="text-[12px] text-muted">{entry.reason}</div>}
                </div>
                <button
                  className="text-[11px] text-danger/80 hover:text-danger"
                  onClick={() => removeEgress(entry.domain)}
                  data-testid={`egress-remove-${entry.domain}`}
                >
                  {t("Remove")}
                </button>
              </div>
            ))}
          </div>
        )}

        <div className="text-[12px] text-muted mt-3 pt-3 border-t border-line" data-testid="openshell-status">
          {openshell
            ? t("OpenShell sandbox backend: {state} · prefix {prefix}", {
                state: openshell.available ? t("available") : t("not detected"),
                prefix: openshell.prefix.join(" "),
              })
            : t("Checking OpenShell status…")}
        </div>
      </div>

      {/* Findings tracker */}
      <div className={CARD + " p-4 mb-4"} data-testid="findings-card">
        <div className={FIELD_LABEL}>{t("Findings")}</div>
        <div className={FIELD_HELP}>
          {t("Each real finding is logged with its fix PR and carried to closed — remediation is tracked, not just reported.")}
        </div>

        {/* Add finding */}
        <div className="grid grid-cols-2 gap-2 mt-3">
          <input
            className={INPUT}
            placeholder={t("Finding title")}
            value={draft.title}
            onChange={(e) => setDraft({ ...draft, title: e.target.value })}
            data-testid="finding-title"
          />
          <select
            className={INPUT}
            value={draft.severity}
            onChange={(e) => setDraft({ ...draft, severity: e.target.value })}
            data-testid="finding-severity"
          >
            {["critical", "high", "medium", "low", "noise"].map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
          <input
            className={INPUT}
            placeholder={t("File path")}
            value={draft.file}
            onChange={(e) => setDraft({ ...draft, file: e.target.value })}
          />
          <input
            className={INPUT}
            placeholder={t("Description (reachability / impact)")}
            value={draft.description}
            onChange={(e) => setDraft({ ...draft, description: e.target.value })}
          />
          <input
            className={INPUT}
            placeholder={t("Fix PR url")}
            value={draft.pr_url}
            onChange={(e) => setDraft({ ...draft, pr_url: e.target.value })}
          />
          <button
            className={BTN_ACCENT}
            disabled={busy || !draft.title.trim()}
            onClick={addFinding}
            data-testid="finding-add"
          >
            {busy ? t("Adding…") : t("Log finding")}
          </button>
        </div>

        {msg && <div className="text-[13px] text-red-500 mt-2">{msg}</div>}

        {findings.length === 0 ? (
          <div className="text-[12px] text-muted mt-3" data-testid="findings-empty">
            {t("No tracked findings yet — security coworkers log them here as they triage.")}
          </div>
        ) : (
          <div className="mt-3 divide-y divide-line">
            {findings.map((f) => (
              <div key={f.id} className="py-2.5 flex items-start gap-3" data-testid={`finding-${f.id}`}>
                <span
                  className={
                    "text-[10px] uppercase tracking-wide font-semibold px-1.5 py-0.5 rounded-full border shrink-0 mt-0.5 " +
                    (SEVERITY_STYLE[f.severity] || SEVERITY_STYLE.medium)
                  }
                >
                  {f.severity}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="text-[13px] font-medium">{f.title}</div>
                  {f.file && <div className="text-[12px] font-mono text-faint">{f.file}</div>}
                  {f.description && <div className="text-[12px] text-muted">{f.description}</div>}
                  {f.pr_url && (
                    <div className="text-[12px] text-accent truncate">{f.pr_url}</div>
                  )}
                  <div className="flex items-center gap-2 mt-1.5">
                    {["open", "fixing", "fixed", "closed"].map((st) => (
                      <button
                        key={st}
                        className={
                          "text-[11px] px-2 py-0.5 rounded-full border " +
                          (f.status === st
                            ? "border-accent text-accent bg-accentSoft"
                            : "border-line text-muted hover:border-lineStrong")
                        }
                        onClick={() => setStatus(f, st)}
                        data-testid={`finding-status-${f.id}-${st}`}
                      >
                        {st}
                      </button>
                    ))}
                    <button
                      className="text-[11px] text-danger/80 hover:text-danger ml-auto"
                      onClick={() => remove(f)}
                      data-testid={`finding-delete-${f.id}`}
                    >
                      {t("Delete")}
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}