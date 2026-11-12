/**
 * Exact decimal arithmetic on the API's amount strings, for display-only totals (estimates and
 * forecasts). Never floating point: "0.10" + "0.20" is "0.30". Money that's stored is always
 * computed by the API.
 */

function scale(value: string, places: number): bigint {
  const negative = value.startsWith("-");
  const [whole = "0", fraction = ""] = value.replace(/^-/, "").split(".");
  const digits = BigInt(`${whole}${fraction.padEnd(places, "0").slice(0, places)}` || "0");
  return negative ? -digits : digits;
}

function places(values: string[]): number {
  return Math.max(0, ...values.map((v) => (v.split(".")[1] ?? "").length));
}

function unscale(value: bigint, digits: number): string {
  const negative = value < BigInt(0);
  const text = (negative ? -value : value).toString().padStart(digits + 1, "0");
  const body = digits ? `${text.slice(0, -digits)}.${text.slice(-digits)}` : text;
  return negative ? `-${body}` : body;
}

/** The exact sum of decimal strings, with as many decimals as the most precise one. */
export function addAmounts(values: string[]): string {
  if (values.length === 0) return "0";
  const digits = places(values);
  return unscale(
    values.reduce((sum, v) => sum + scale(v, digits), BigInt(0)),
    digits,
  );
}

/** a - b, exactly. */
export function subtractAmounts(a: string, b: string): string {
  return addAmounts([a, b.startsWith("-") ? b.slice(1) : `-${b}`]);
}

/** part / whole as a percentage for display (one decimal), or null when whole isn't positive. */
export function percentOf(part: string, whole: string): number | null {
  const digits = places([part, whole]);
  const w = scale(whole, digits);
  if (w <= BigInt(0)) return null;
  const tenths = (scale(part, digits) * BigInt(1000)) / w;
  return Number(tenths) / 10;
}
