import { describe, expect, it } from "vitest";
import { interpolate, translate } from "./index";
import { zhDict } from "./zh";
import { zhApp } from "./zh/app";
import { zhShell } from "./zh/shell";
import { zhSettings } from "./zh/settings";
import { zhCards } from "./zh/cards";

// Guards for the zero-dependency i18n setup. The tsc layer (MessageKey) catches
// literal-key typos; these tests catch what the type system can't see: empty or
// drifted translations, and the merge order accidentally shadowing one file's
// entry with another's.

describe("interpolate", () => {
  it("replaces named tokens", () => {
    expect(interpolate("{n} runs", { n: 3 })).toBe("3 runs");
    expect(interpolate("Error: {msg}", { msg: "boom" })).toBe("Error: boom");
  });

  it("leaves unknown tokens untouched and handles missing params", () => {
    expect(interpolate("{a} and {b}", { a: 1 })).toBe("1 and {b}");
    expect(interpolate("plain")).toBe("plain");
  });
});

describe("translate (module-level, EN default)", () => {
  it("falls back to the key itself in English mode", () => {
    expect(translate("Send message")).toBe("Send message");
  });

  it("applies interpolation to the key in English mode", () => {
    expect(translate("{n} runs", { n: 2 })).toBe("2 runs");
  });
});

describe("zh dictionary integrity", () => {
  it("has a substantial merged dictionary", () => {
    expect(Object.keys(zhDict).length).toBeGreaterThan(700);
  });

  it("every entry translates to a non-empty string", () => {
    for (const [key, value] of Object.entries(zhDict)) {
      expect(typeof value, `key ${key} has a non-string value`).toBe("string");
      expect(value.trim().length, `key ${key} has an empty translation`).toBeGreaterThan(0);
    }
  });

  it("keys duplicated across area files carry the SAME value (no silent shadowing)", () => {
    // zh/index.ts merges with object spread — a conflicting duplicate would be
    // resolved by file order and silently drop one translation.
    const areas: [string, Record<string, string>][] = [
      ["app", zhApp],
      ["shell", zhShell],
      ["settings", zhSettings],
      ["cards", zhCards],
    ];
    const seen = new Map<string, string>();
    for (const [file, dict] of areas) {
      for (const [key, value] of Object.entries(dict)) {
        const prev = seen.get(key);
        if (prev !== undefined) {
          expect(value, `key "${key}" differs between ${file} and its earlier definition`).toBe(prev);
        } else {
          seen.set(key, value);
        }
      }
    }
  });
});
