import { describe, expect, it } from "vitest";

import { formatEur, parseEuroInput, toEuroInput } from "@/lib/money";

describe("parseEuroInput", () => {
  it.each([
    ["12", "12.00"],
    ["12,5", "12.50"],
    ["12,50", "12.50"],
    ["-150,00", "-150.00"],
    ["1.234,56", "1234.56"],
    ["1.234", "1234.00"],
    ["12.50", "12.50"],
    [" 7,1 ", "7.10"],
    ["0,99", "0.99"],
    ["123456789,00", "123456789.00"],
  ])("%s → %s", (input, expected) => {
    expect(parseEuroInput(input)).toBe(expected);
  });

  it.each(["", "  ", "abc", "12,505", "1,2,3", "1.23.4", "1234567890", "1.234.567.890", "12,", "-"])(
    "rejects %j",
    (input) => {
      expect(parseEuroInput(input)).toBeNull();
    },
  );
});

describe("display", () => {
  it("formats de-DE EUR and back to input", () => {
    expect(formatEur("1234.56").replace(/\s/g, " ")).toBe("1.234,56 €");
    expect(toEuroInput("12.50")).toBe("12,50");
    expect(toEuroInput(null)).toBe("");
  });
});
