export type Anlage = "N" | "Sonderausgaben" | "agB" | "§35a" | "Kind" | "KAP" | "V";

export interface TaxItem {
  id: string;
  vendor: string;
  date: string;
  gross: number;
  deductible: number;
  labour35a?: number;
  anlage: Anlage;
  zeile: string;
  category: string;
  person: string;
  channel: "web" | "telegram";
  confidence: number;
  relevant: boolean;
  overridden?: boolean;
}

export const members = [
  { name: "Tselmeg", kind: "adult", dob: "1988-04-12", steuerklasse: "III", employer: "Siemens AG", commuteKm: 18, homeofficeDays: 96 },
  { name: "Anna", kind: "adult", dob: "1990-09-03", steuerklasse: "V", employer: "Stadt München", commuteKm: 7, homeofficeDays: 40 },
  { name: "Mila", kind: "child", dob: "2019-02-21", kindergeldMonths: 12, betreuung: 2160 },
];

export const items: TaxItem[] = [
  { id: "1", vendor: "Malerbetrieb Huber", date: "2025-03-14", gross: 1312.4, deductible: 720, labour35a: 720, anlage: "§35a", zeile: "Z. 7", category: "Handwerkerleistung", person: "Haushalt", channel: "telegram", confidence: 0.94, relevant: true },
  { id: "2", vendor: "Kita Sonnenschein", date: "2025-12-31", gross: 2160, deductible: 1440, anlage: "Kind", zeile: "Z. 64", category: "Kinderbetreuung", person: "Mila", channel: "web", confidence: 0.98, relevant: true },
  { id: "3", vendor: "Apple Store", date: "2025-05-02", gross: 1499, deductible: 1499, anlage: "N", zeile: "Z. 46", category: "Arbeitsmittel (Laptop)", person: "Tselmeg", channel: "web", confidence: 0.71, relevant: true, overridden: true },
  { id: "4", vendor: "ADAC Versicherung", date: "2025-01-15", gross: 389.5, deductible: 389.5, anlage: "Sonderausgaben", zeile: "Z. 49", category: "Haftpflicht", person: "Haushalt", channel: "telegram", confidence: 0.88, relevant: true },
  { id: "5", vendor: "Zahnarztpraxis Dr. Lenz", date: "2025-08-22", gross: 860, deductible: 860, anlage: "agB", zeile: "Z. 4", category: "Krankheitskosten", person: "Anna", channel: "telegram", confidence: 0.9, relevant: true },
  { id: "6", vendor: "Comdirect", date: "2026-02-10", gross: 412.3, deductible: 412.3, anlage: "KAP", zeile: "Z. 7", category: "Jahressteuerbescheinigung", person: "Tselmeg", channel: "web", confidence: 0.97, relevant: true },
  { id: "7", vendor: "Gebäudereinigung Blitz", date: "2025-11-30", gross: 540, deductible: 540, labour35a: 540, anlage: "§35a", zeile: "Z. 4", category: "Haushaltsnahe Dienstleistung", person: "Haushalt", channel: "web", confidence: 0.62, relevant: true },
  { id: "8", vendor: "REWE", date: "2025-06-03", gross: 54.12, deductible: 0, anlage: "N", zeile: "—", category: "Lebensmittel", person: "Haushalt", channel: "telegram", confidence: 0.99, relevant: false },
  { id: "9", vendor: "Verdi", date: "2025-12-01", gross: 312, deductible: 312, anlage: "N", zeile: "Z. 41", category: "Gewerkschaftsbeitrag", person: "Anna", channel: "web", confidence: 0.95, relevant: true },
];

export const missing = [
  { title: "Lohnsteuerbescheinigung Anna", detail: "Ohne sie ist die Schätzung ungenau (Anlage N, Z. 3–6)." },
  { title: "Nebenkostenabrechnung 2025", detail: "Enthält oft §35a-Anteile (Hausmeister, Treppenreinigung)." },
];

export const estimate = {
  year: 2025,
  refund: 2184.37,
  zvE: 98420,
  festgesetzt: 18631,
  lohnsteuerPaid: 20815.37,
  soli: 0,
  kirche: 0,
  assessment: "Zusammenveranlagung",
  pflicht: true,
};

export const eur = (n: number) =>
  n.toLocaleString("de-DE", { style: "currency", currency: "EUR" });

export const anlageLabel: Record<Anlage, string> = {
  N: "Anlage N · Werbungskosten",
  Sonderausgaben: "Sonderausgaben",
  agB: "Außergewöhnliche Belastungen",
  "§35a": "§35a Haushaltsnahe Aufwendungen",
  Kind: "Anlage Kind",
  KAP: "Anlage KAP",
  V: "Anlage V",
};
