// zh-CN dictionary, split by UI area so parallel work doesn't collide.
// Keys are the exact English source strings; missing key = English fallback.
// No Record annotation: the inferred literal key union IS the MessageKey type,
// so a t() call with a key absent from every dictionary is a tsc error.
import { zhApp } from "./app";
import { zhShell } from "./shell";
import { zhSettings } from "./settings";
import { zhCards } from "./cards";

export const zhDict = {
  ...zhApp,
  ...zhShell,
  ...zhSettings,
  ...zhCards,
};

/** Every translatable string key. A key present in two dicts with different
 * values is NOT a type error (spread: last file wins) — the unit test guards
 * that invariant instead. */
export type MessageKey = keyof typeof zhDict;
