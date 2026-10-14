import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DocThumb } from "@/components/documents/DocThumb";

describe("DocThumb", () => {
  it("shows the preview with an alt text without the file name", () => {
    render(<DocThumb documentId="abc" fallback={<span>icon</span>} />);
    const img = screen.getByRole("img", { name: "Vorschau des Belegs" });
    expect(img.getAttribute("src")).toBe("/api/documents/abc/preview");
  });

  it("falls back to the icon when there is no preview yet", () => {
    render(<DocThumb documentId="abc" retryKey="queued" fallback={<span>icon</span>} />);
    fireEvent.error(screen.getByRole("img"));
    expect(screen.getByText("icon")).toBeTruthy();
    expect(screen.queryByRole("img")).toBeNull();
  });
});
