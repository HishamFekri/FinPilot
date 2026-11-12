import { describe, expect, it } from "vitest";
import { accountForCurrency, orderCategories } from "@/components/app/finance/quick-add";
import { addAmounts, percentOf, subtractAmounts } from "./decimal";
import type { Category } from "./finance";
import { ratioPercent } from "./installments";

describe("exact display sums", () => {
  it("adds without floating-point error", () => {
    expect(addAmounts(["0.10", "0.20"])).toBe("0.30");
    expect(addAmounts(["1000", "0.5", "-0.25"])).toBe("1000.25");
    expect(addAmounts([])).toBe("0");
  });
  it("subtracts, including below zero", () => {
    expect(subtractAmounts("100.00", "150.50")).toBe("-50.50");
    expect(subtractAmounts("2400.00", "-10")).toBe("2410.00");
  });
  it("gives a percentage only when there's something to divide by", () => {
    expect(percentOf("500", "2000")).toBe(25);
    expect(percentOf("1", "3")).toBe(33.3);
    expect(percentOf("5", "0")).toBeNull();
  });
});

describe("what-if ratio", () => {
  it("is the monthly share of income, one decimal", () => {
    expect(ratioPercent(500, 2000)).toBe(25);
    expect(ratioPercent(700, 2000)).toBe(35);
    expect(ratioPercent(500, 0)).toBeNull();
  });
});

describe("Quick Add account for a currency", () => {
  const accounts = [
    { id: "cash", currency: "TRY", status: "active" },
    { id: "old-usd", currency: "USD", status: "archived" },
    { id: "usd-1", currency: "USD", status: "active" },
    { id: "usd-2", currency: "USD", status: "active" },
  ];
  it("prefers the default account, then the first active one in that currency", () => {
    expect(accountForCurrency(accounts, "USD", "usd-2")).toBe("usd-2");
    expect(accountForCurrency(accounts, "USD", "cash")).toBe("usd-1");
  });
  it("never picks an archived account, and says when none fits", () => {
    expect(accountForCurrency(accounts, "EUR", "cash")).toBeNull();
    expect(accountForCurrency([accounts[1]!], "USD", null)).toBeNull();
  });
});

describe("category order", () => {
  const c = (id: string, extra: Partial<Category> = {}): Category =>
    ({ id, kind: "expense", system_key: id, name: null, icon: "tag", color: "sand",
       status: "active", version: 1, ...extra }) as Category; // prettier-ignore
  it("puts favourites first, then the most recently used", () => {
    const ordered = orderCategories([
      c("food"),
      c("rent", { last_used_at: "2026-11-01T00:00:00Z" }),
      c("fun", { is_favourite: true }),
      c("bus", { last_used_at: "2026-11-05T00:00:00Z" }),
    ]);
    expect(ordered.map((x) => x.id)).toEqual(["fun", "bus", "rent", "food"]);
  });
});
