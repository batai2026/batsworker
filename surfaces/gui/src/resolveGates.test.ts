import { describe, expect, it } from "vitest";
import {
  resolveLastApproval,
  resolveLastDirReq,
  resolveLastItemsReq,
  resolveLastPlan,
  resolveLastQuestion,
  resolveLastTeam,
  resolveLastToolReq,
} from "./resolveGates";
import type { Item } from "./types";

function item(kind: string, tag: string, resolved?: unknown): Item {
  const base = { ts: 0 } as any;
  if (kind === "approval")
    return { ...base, kind, name: `tool-${tag}`, args: {}, reason: "r", resolved };
  if (kind === "dirreq")
    return { ...base, kind, path: `/ws/${tag}`, writable: true, resolved };
  if (kind === "toolreq")
    return { ...base, kind, tool: tag, resolved };
  if (kind === "planreq")
    return { ...base, kind, plan: `plan-${tag}`, resolved };
  if (kind === "teamreq")
    return { ...base, kind, team: `team-${tag}`, resolved };
  if (kind === "itemsreq")
    return { ...base, kind, workspace: `/ws/${tag}`, items: [], resolved };
  if (kind === "question")
    return { ...base, kind, question: `q-${tag}`, options: [], allow_text: false, multi: false, header: "", questions: [], resolved };
  return { ...base, kind, text: tag };
}

describe("resolveGates", () => {
  it("resolves only the NEWEST unresolved gate of the matching kind", () => {
    const items: Item[] = [
      item("approval", "old", false),
      item("approval", "new", false),
    ];
    const out = resolveLastApproval(items, { decision: "allow" } as any);
    expect((out[0] as any).resolved).toBe(false); // old stays unresolved
    expect((out[1] as any).resolved).toMatchObject({ decision: "allow" });
  });

  it("leaves other kinds untouched", () => {
    const items: Item[] = [item("planreq", "p", false), item("approval", "a", false)];
    const out = resolveLastPlan(items, "approved");
    expect((out[0] as any).resolved).toBe("approved");
    expect((out[1] as any).resolved).toBe(false); // approval untouched
  });

  it("covers every gate kind", () => {
    const dir = resolveLastDirReq([item("dirreq", "d", false)], "granted");
    expect((dir[0] as any).resolved).toBe("granted");

    const tool = resolveLastToolReq([item("toolreq", "t", false)], "installed");
    expect((tool[0] as any).resolved).toBe("installed");

    const team = resolveLastTeam([item("teamreq", "tm", false)], "approved");
    expect((team[0] as any).resolved).toBe("approved");

    const itemsReq = resolveLastItemsReq([item("itemsreq", "w", false)], "approved");
    expect((itemsReq[0] as any).resolved).toBe("approved");

    const question = resolveLastQuestion([item("question", "q", false)], "yes");
    expect((question[0] as any).resolved).toBe("yes");
  });
});