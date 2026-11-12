"use client";

import { useEffect, useState } from "react";
import { rememberCurrency, rememberedCurrency } from "@/lib/balances";
import { useUser } from "../auth-provider";
import { useFinanceState } from "../finance/finance-provider";
import { Select } from "../select";

const COMMON = ["TRY", "USD", "EUR", "GBP"];

/** The display currency for totals: remembered on this device, else the person's own currency. */
export function useDisplayCurrency(): [string, (code: string) => void] {
  const user = useUser();
  const fallback = user.preferred_currency ?? "USD";
  const [code, setCode] = useState(fallback);
  useEffect(() => {
    // Read after mounting: the server can't know what this device remembered.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setCode(rememberedCurrency(fallback));
  }, [fallback]);
  return [
    code,
    (next: string) => {
      setCode(next);
      rememberCurrency(next);
    },
  ];
}

export function CurrencyPicker({
  value,
  onChange,
  label,
  className,
}: {
  value: string;
  onChange: (code: string) => void;
  label: string;
  className?: string;
}) {
  // Accounts if they're loaded; the Accounts page renders outside the finance gate.
  const state = useFinanceState();
  const accounts = state?.load.kind === "ready" ? state.load.accounts : [];
  const user = useUser();
  const codes = [
    ...new Set([
      value,
      ...(user.preferred_currency ? [user.preferred_currency] : []),
      ...accounts.map((a) => a.currency),
      ...COMMON,
    ]),
  ];
  return (
    <Select
      size="sm"
      value={value}
      onValueChange={onChange}
      aria-label={label}
      dir="ltr"
      className={className}
    >
      {codes.map((c) => (
        <option key={c} value={c}>
          {c}
        </option>
      ))}
    </Select>
  );
}
