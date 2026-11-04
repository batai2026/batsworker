import { useEffect, useMemo, useState } from "react";
import {
  getAutomationTemplates,
  getConnectors,
  instantiateAutomationTemplate,
  type AutomationTemplate,
  type Connector,
} from "../api";
import { ConnectorBadge } from "../connectors/ConnectorIcon";
import { useI18n } from "../i18n";

// The automation template marketplace: a browsable, searchable catalog of
// backend-served templates (the Cursor-Automations-style door). Each card shows
// title, blurb, cadence, category, and the connectors it needs; picking one
// expands a configure pane (repo/channel/delivery/day/time) and "Create" calls
// the server's one-click instantiate endpoint, which renders the instructions
// and creates the automation with the right standing grants.

const CARD = "rounded-xl2 border border-line bg-panel";
const BTN_ACCENT =
  "text-[13px] px-3 py-2 rounded-lg bg-accent text-white shrink-0 disabled:opacity-40";
const BTN_BORDERED =
  "text-[13px] px-3 py-2 rounded-lg border border-line bg-paper hover:border-lineStrong shrink-0";
const INPUT =
  "w-full min-w-0 px-3 py-2 rounded-lg border border-line bg-paper text-[13px] text-ink outline-none focus:border-accent";
const CHIP =
  "text-[11px] px-2 py-0.5 rounded-full border border-line bg-paper text-muted shrink-0";

const DAYS: { value: string; label: string }[] = [
  { value: "mon", label: "Mondays" },
  { value: "tue", label: "Tuesdays" },
  { value: "wed", label: "Wednesdays" },
  { value: "thu", label: "Thursdays" },
  { value: "fri", label: "Fridays" },
  { value: "sat", label: "Saturdays" },
  { value: "sun", label: "Sundays" },
  { value: "weekdays", label: "Weekdays" },
  { value: "daily", label: "Every day" },
];

export function TemplateMarketplace({
  onCreated,
}: {
  // Called with the new task id after a template is instantiated (the parent
  // refreshes the list and opens the task detail).
  onCreated: (taskId: string) => void;
}) {
  const { t } = useI18n();
  const [templates, setTemplates] = useState<AutomationTemplate[]>([]);
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<string>("all");
  const [pickedKey, setPickedKey] = useState<string | null>(null);
  const [repo, setRepo] = useState("");
  const [channel, setChannel] = useState("");
  const [deliver, setDeliver] = useState<"app" | "slack">("app");
  const [day, setDay] = useState("daily");
  const [time, setTime] = useState("09:00");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    getAutomationTemplates().then(setTemplates).catch(() => setTemplates([]));
    getConnectors().then(setConnectors).catch(() => setConnectors([]));
  }, []);

  const picked = useMemo(
    () => templates.find((x) => x.key === pickedKey) || null,
    [templates, pickedKey],
  );

  const categories = useMemo(() => {
    const set = new Set<string>();
    for (const tmpl of templates) if (tmpl.category) set.add(tmpl.category);
    return ["all", ...Array.from(set).sort()];
  }, [templates]);

  const q = query.trim().toLowerCase();
  const visible = templates.filter(
    (tmpl) =>
      (category === "all" || tmpl.category === category) &&
      (!q ||
        tmpl.title.toLowerCase().includes(q) ||
        tmpl.blurb.toLowerCase().includes(q) ||
        tmpl.key.toLowerCase().includes(q)),
  );

  const pick = (tmpl: AutomationTemplate) => {
    setPickedKey(tmpl.key === pickedKey ? null : tmpl.key);
    setError("");
    // Reset per-template config but keep the user's day/time where sensible.
    setRepo("");
    setChannel("");
    setDay(tmpl.day || "daily");
    setTime(tmpl.time || "09:00");
  };

  const create = async () => {
    if (!picked) return;
    setBusy(true);
    setError("");
    try {
      const res = await instantiateAutomationTemplate(picked.key, {
        repo,
        channel,
        deliver,
        day,
        time,
      });
      if (res.ok && res.task) {
        onCreated(res.task.id);
        return;
      }
      setError(res.error || "Could not create the automation.");
    } catch {
      setError("Could not create the automation.");
    } finally {
      setBusy(false);
    }
  };

  const canCreate =
    !!picked &&
    (!picked.needs_repo || repo.trim().length > 0) &&
    (!picked.needs_channel || channel.trim().length > 0);

  return (
    <div className={CARD + " p-4 mb-4"} data-testid="template-marketplace">
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <div className="min-w-0 flex-1">
          <div className="text-[13px] font-semibold">Template marketplace</div>
          <div className="text-[12px] text-muted">
            {t("Curated automations — pick one, configure it, and it runs on a schedule.")}
          </div>
        </div>
        <input
          className={"w-44 px-3.5 py-1.5 rounded-full border border-line bg-paper text-[13px] outline-none focus:border-accent"}
          placeholder={t("Search templates")}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          data-testid="tmpl-search"
        />
      </div>

      <div className="flex flex-wrap gap-1.5 mb-3">
        {categories.map((c) => (
          <button
            key={c}
            type="button"
            className={
              "text-[12px] px-3 py-1 rounded-full border " +
              (category === c
                ? "border-accent text-accent bg-accentSoft"
                : "border-line text-muted hover:border-lineStrong")
            }
            onClick={() => setCategory(c)}
          >
            {c === "all" ? t("All") : c}
          </button>
        ))}
      </div>

      {visible.length === 0 ? (
        <div className="text-[13px] text-muted py-3">{t("No templates match.")}</div>
      ) : (
        <div className="grid grid-cols-3 auto-rows-fr gap-3">
          {visible.map((tmpl) => (
            <button
              key={tmpl.key}
              data-testid={`marketplace-template-${tmpl.key}`}
              className={
                "h-full text-left rounded-xl2 border bg-panel p-4 flex flex-col gap-1.5 " +
                (pickedKey === tmpl.key
                  ? "border-accent ring-2 ring-accentSoft"
                  : "border-line hover:border-lineStrong")
              }
              onClick={() => pick(tmpl)}
            >
              <span className="flex items-center gap-1.5">
                <span className="text-[13px] font-semibold flex-1">{tmpl.title}</span>
                <span className={CHIP}>{tmpl.category}</span>
              </span>
              <span className="text-[12px] text-muted leading-relaxed flex-1">{tmpl.blurb}</span>
              <span className="flex items-center gap-1.5 mt-1">
                {tmpl.conns.map((c) => {
                  const cs = connectors.find((x) => x.name === c.name);
                  const on = !!cs?.connected;
                  return (
                    <span
                      key={c.name}
                      title={`${cs?.title || c.name} — ${on ? t("connected") : t("not connected yet")}`}
                      style={on ? undefined : { filter: "grayscale(1)", opacity: 0.55 }}
                    >
                      {cs ? (
                        <ConnectorBadge connector={cs} size={16} title={cs.title} />
                      ) : (
                        <span className="inline-block w-4 h-4 rounded-full border border-line2" />
                      )}
                    </span>
                  );
                })}
                <span className="text-[11px] text-faint ml-0.5">
                  {tmpl.conns.length === 0 ? `${t("No connections needed")} · ` : ""}
                  {tmpl.cadence}
                </span>
              </span>
            </button>
          ))}
        </div>
      )}

      {picked && (
        <div className="mt-3 rounded-xl2 border border-line bg-paper p-4" data-testid="tmpl-configure">
          <div className="flex items-baseline gap-2 pb-2.5 mb-3 border-b border-line">
            <span className="text-[11px] uppercase tracking-[0.05em] text-accent font-semibold">
              {t("Set up")}
            </span>
            <span className="text-[14px] font-semibold">{picked.title}</span>
            <span className="ml-auto text-[12px] text-faint">
              {picked.cadence} · {picked.category}
            </span>
          </div>

          {picked.needs_repo && (
            <label className="block mb-3">
              <span className="block text-[12px] text-muted mb-1">{t("Repository")}</span>
              <input
                className={INPUT}
                value={repo}
                placeholder="owner/repo"
                onChange={(e) => setRepo(e.target.value)}
                data-testid="tmpl-repo"
              />
            </label>
          )}

          {picked.needs_channel && (
            <label className="block mb-3">
              <span className="block text-[12px] text-muted mb-1">{t("Slack channel")}</span>
              <input
                className={INPUT}
                value={channel}
                placeholder="#team-channel"
                onChange={(e) => setChannel(e.target.value)}
                data-testid="tmpl-channel"
              />
            </label>
          )}

          {picked.deliver && (
            <div className="mb-3">
              <span className="block text-[12px] text-muted mb-1">{t("Deliver to")}</span>
              <div className="flex gap-2">
                {(["app", "slack"] as const).map((d) => (
                  <button
                    key={d}
                    type="button"
                    className={
                      "text-[12px] px-3 py-1.5 rounded-full border " +
                      (deliver === d
                        ? "border-accent text-accent bg-accentSoft"
                        : "border-line text-muted hover:border-lineStrong")
                    }
                    onClick={() => setDeliver(d)}
                  >
                    {d === "app" ? t("App deliverable") : t("Slack DM")}
                  </button>
                ))}
              </div>
            </div>
          )}

          <div className="tmpl-sched">
            <label className="tmpl-field">
              <span>{t("At")}</span>
              <input
                type="time"
                className={"tmpl-input tmpl-time"}
                value={time}
                onChange={(e) => setTime(e.target.value)}
              />
            </label>
            <label className="tmpl-field">
              <span>{t("Repeat")}</span>
              <select
                className={"tmpl-input tmpl-select"}
                value={day}
                onChange={(e) => setDay(e.target.value)}
              >
                {DAYS.map((d) => (
                  <option key={d.value} value={d.value}>
                    {d.label}
                  </option>
                ))}
              </select>
            </label>
          </div>

          {picked.consent && picked.needs_channel && (
            <div className="text-[12px] text-muted mt-2 mb-2">
              {t("This template posts to {channel} — it may send messages there without asking each time.", {
                channel: channel.trim() || "#channel",
              })}
            </div>
          )}

          {error ? (
            <div className="text-[13px] text-red-500 mt-2" role="alert">
              {error}
            </div>
          ) : null}

          <div className="flex gap-2 mt-3">
            <button className={BTN_ACCENT} disabled={!canCreate || busy} onClick={create} data-testid="tmpl-create">
              {busy ? t("Creating…") : t("Create automation")}
            </button>
            <button className={BTN_BORDERED} onClick={() => setPickedKey(null)}>
              {t("Cancel")}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}