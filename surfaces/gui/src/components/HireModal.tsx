import { useState } from "react";
import { useI18n } from "../i18n";
import { Icon } from "./Icon";

// Role-based "hire" landing pages (Lindy-style): instead of a flat list, browse
// coworkers by the JOB you need done. Each role card carries the landing copy —
// what it does, what it checks in before, what it never does, connectors it needs —
// and "Meet the coworker →" opens that persona's detail page, where you can start
// a session or configure it.

interface HireRole {
  key: string;
  title: string;
  tagline: string;
  does: string[];
  checksIn: string;
  never: string[];
  connectors: string[];
  personaId: string;
  personaName: string;
}

const ROLES: HireRole[] = [
  {
    key: "security",
    title: "Security Reviewer",
    tagline: "Find and fix security issues — scan, triage, PR",
    does: [
      "Drives semgrep and secret scanners across your codebase",
      "Triages findings by reachability, not severity labels",
      "Opens focused fix PRs that match your code's idiom",
    ],
    checksIn: "before opening a pull request",
    never: [
      "never prints a discovered secret's value",
      "never disables a check to silence a warning",
      "never merges its own security PR",
    ],
    connectors: ["github"],
    personaId: "security",
    personaName: "Security Coworker",
  },
  {
    key: "cloud",
    title: "Cloud Posture",
    tagline: "Review Terraform & cloud config — read-only, evidence first",
    does: [
      "Scans Terraform and IaC with trivy/checkov",
      "Verifies posture against the live account, read-only",
      "Fixes risk in the IaC and attaches terraform plan",
    ],
    checksIn: "before opening a fix PR",
    never: [
      "never writes to your cloud account",
      "never runs terraform apply",
      "never prints cloud credentials",
    ],
    connectors: ["github"],
    personaId: "cloud-posture",
    personaName: "Cloud Posture Coworker",
  },
  {
    key: "dep-audit",
    title: "Dependency Audit",
    tagline: "Vulnerable dependencies — audit, minimal upgrades, PRs",
    does: [
      "Scans lockfiles with osv-scanner, npm audit, pip-audit",
      "Separates exploitable from theoretical",
      "Ships minimal, test-verified upgrade PRs",
    ],
    checksIn: "before touching a lockfile",
    never: [
      "never bumps past the smallest safe upgrade",
      "never merges on red tests",
      "never rewrites locks by hand",
    ],
    connectors: ["github"],
    personaId: "dep-audit",
    personaName: "Dependency Audit Coworker",
  },
  {
    key: "assistant",
    title: "Executive Assistant",
    tagline: "Your everyday admin — calendar, email, files, Slack",
    does: [
      "Prepares briefs, drafts replies, and untangles your calendar",
      "Works across email, calendar, Slack, and your files",
      "Produces polished deliverables you can open and share",
    ],
    checksIn: "before sending any message or changing your calendar",
    never: [
      "never acts on a connected service without asking",
      "never deletes your data",
    ],
    connectors: ["gmail", "google_calendar", "slack"],
    personaId: "cowork",
    personaName: "Cowork (default assistant)",
  },
  {
    key: "engineering",
    title: "Software Engineer",
    tagline: "Plan, implement, and review code in your repo",
    does: [
      "Reads and edits code with line-numbered context",
      "Runs git, greps, and the test suite",
      "Fans out read-only explorer subagents for big questions",
    ],
    checksIn: "before running shell commands or writing files",
    never: [
      "never writes outside your chosen folder",
      "never bypasses approvals",
    ],
    connectors: ["github"],
    personaId: "code",
    personaName: "Code Coworker",
  },
];

const CARD = "rounded-xl2 border border-line bg-panel";
const BTN_ACCENT =
  "text-[13px] px-3 py-2 rounded-lg bg-accent text-white shrink-0 disabled:opacity-40";
const CHIP =
  "text-[11px] px-2 py-0.5 rounded-full border border-line bg-paper text-muted shrink-0";

export function HireModal({
  onClose,
  onOpenPersona,
}: {
  onClose: () => void;
  onOpenPersona: (id: string) => void;
}) {
  const { t } = useI18n();
  const [expanded, setExpanded] = useState<string | null>(null);

  const toggle = (key: string) => setExpanded(expanded === key ? null : key);

  return (
    <div className="fixed inset-0 z-50" data-testid="hire-modal">
      <div className="absolute inset-0 bg-black/30 backdrop-blur-[1px]" onClick={onClose} />
      <div className="absolute left-1/2 top-[6vh] -translate-x-1/2 w-[720px] max-w-[94vw] max-h-[88vh] rounded-xl2 border border-line bg-panel shadow-2xl overflow-hidden flex flex-col">
        <div className="px-5 pt-4 pb-3 border-b border-line flex items-center gap-3 shrink-0">
          <div className="min-w-0 flex-1">
            <div className="text-[14px] font-semibold">Hire a coworker</div>
            <div className="text-[12px] text-muted">
              {t("Pick the job, meet the coworker — every one checks in before acting.")}
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

        <div className="overflow-y-auto hairline-scroll p-5">
          <div className="space-y-3">
            {ROLES.map((r) => (
              <div key={r.key} className={CARD + " overflow-hidden"} data-testid={`hire-role-${r.key}`}>
                <button
                  className="w-full text-left p-4 flex items-center gap-3 hover:bg-paper/50"
                  onClick={() => toggle(r.key)}
                >
                  <span className="min-w-0 flex-1">
                    <span className="block text-[14px] font-semibold">{r.title}</span>
                    <span className="block text-[12px] text-muted">{r.tagline}</span>
                  </span>
                  <span className={CHIP}>{r.personaName}</span>
                  <span className="text-faint text-[14px] shrink-0">
                    {expanded === r.key ? "▾" : "›"}
                  </span>
                </button>

                {expanded === r.key && (
                  <div className="px-4 pb-4 space-y-3 border-t border-line pt-3" data-testid={`hire-detail-${r.key}`}>
                    <div>
                      <div className="text-[11px] uppercase tracking-[0.05em] text-faint font-semibold mb-1">
                        {t("What it does")}
                      </div>
                      <ul className="text-[13px] text-ink space-y-1">
                        {r.does.map((d) => (
                          <li key={d} className="flex gap-1.5">
                            <span className="text-ok shrink-0">✓</span> {d}
                          </li>
                        ))}
                      </ul>
                    </div>
                    <div>
                      <div className="text-[11px] uppercase tracking-[0.05em] text-faint font-semibold mb-1">
                        {t("Checks in")}
                      </div>
                      <div className="text-[13px] text-muted">{r.checksIn}</div>
                    </div>
                    <div>
                      <div className="text-[11px] uppercase tracking-[0.05em] text-faint font-semibold mb-1">
                        {t("Never")}
                      </div>
                      <ul className="text-[13px] text-muted space-y-1">
                        {r.never.map((n) => (
                          <li key={n} className="flex gap-1.5">
                            <span className="text-danger shrink-0">✕</span> {n}
                          </li>
                        ))}
                      </ul>
                    </div>
                    <div>
                      <div className="text-[11px] uppercase tracking-[0.05em] text-faint font-semibold mb-1">
                        {t("Works with")}
                      </div>
                      <div className="flex flex-wrap gap-1.5">
                        {r.connectors.map((c) => (
                          <span key={c} className={CHIP}>{c}</span>
                        ))}
                      </div>
                    </div>
                    <button
                      className={BTN_ACCENT}
                      data-testid={`hire-open-${r.personaId}`}
                      onClick={() => onOpenPersona(r.personaId)}
                    >
                      {t("Meet the coworker →")}
                    </button>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}