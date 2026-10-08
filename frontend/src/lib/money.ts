/**
 * Money on the wire is a dot-decimal string (`"1234.56"`, `^-?\d{1,9}\.\d{2}$`); the user types
 * German amounts. Pure helpers, no I/O.
 */

const GROUPED = /^\d{1,3}(\.\d{3})+$/; // 1.234 / 1.234.567 (thousands separators only)

/**
 * German input → wire string, or `null` when invalid. Accepts `12`, `12,5`, `12,50`,
 * `-150,00`, `1.234,56`, `1.234` and `12.50` (one dot followed by 1–2 digits = decimal).
 */
export function parseEuroInput(raw: string): string | null {
  const text = raw.trim();
  const match = /^(-?)([\d.]+)(?:,(\d{1,2}))?$/.exec(text);
  if (!match) return null;
  const [, sign, whole, cents] = match as unknown as [string, string, string, string | undefined];
  let integer: string;
  let fraction = cents ?? "";
  if (!whole.includes(".")) integer = whole;
  else if (cents === undefined && /^\d+\.\d{1,2}$/.test(whole)) {
    [integer, fraction] = whole.split(".") as [string, string];
  } else if (GROUPED.test(whole)) integer = whole.replaceAll(".", "");
  else return null;
  integer = integer.replace(/^0+(?=\d)/, "");
  if (integer.length > 9) return null;
  return `${sign}${integer}.${fraction.padEnd(2, "0")}`;
}

/** Wire string → what the input shows (`"1234.50"` → `"1234,50"`). */
export function toEuroInput(value: string | null): string {
  return value === null ? "" : value.replace(".", ",");
}

const EUR = new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR" });

/** `"1234.56"` → `1.234,56 €` (string math stays exact; the number is display only). */
export function formatEur(value: string): string {
  return EUR.format(Number(value));
}
