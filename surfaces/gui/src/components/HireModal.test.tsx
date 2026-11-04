import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { HireModal } from "./HireModal";

afterEach(cleanup);

function openRole(testId: string) {
  const card = screen.getByTestId(testId);
  const button = card.querySelector("button");
  if (!button) throw new Error(`no button inside ${testId}`);
  fireEvent.click(button);
}

describe("HireModal — role-based hire landing pages", () => {
  it("renders role cards and expands a card's landing copy", () => {
    render(<HireModal onClose={vi.fn()} onOpenPersona={vi.fn()} />);

    expect(screen.getByTestId("hire-role-security")).toBeTruthy();
    expect(screen.getByTestId("hire-role-cloud")).toBeTruthy();
    expect(screen.getByTestId("hire-role-dep-audit")).toBeTruthy();

    openRole("hire-role-security");
    const detail = screen.getByTestId("hire-detail-security");
    expect(detail.textContent).toContain("What it does");
    expect(detail.textContent).toContain("Checks in");
    expect(detail.textContent).toContain("Never");
    expect(detail.textContent).toContain("Works with");
  });

  it("deep-links to the persona detail on 'Meet the coworker →'", () => {
    const onOpenPersona = vi.fn();
    render(<HireModal onClose={vi.fn()} onOpenPersona={onOpenPersona} />);

    openRole("hire-role-security");
    fireEvent.click(screen.getByTestId("hire-open-security"));

    expect(onOpenPersona).toHaveBeenCalledWith("security");
  });

  it("closes via the ✕ button", () => {
    const onClose = vi.fn();
    render(<HireModal onClose={onClose} onOpenPersona={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalled();
  });
});