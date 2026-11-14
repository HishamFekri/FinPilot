"use client";

import { useLocale, useTranslations } from "next-intl";
import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import type { Locale } from "@/i18n/routing";
import { type Account, accountsApi, type CurrencyInfo } from "@/lib/accounts";
import { type Category, financeApi, type SetupState } from "@/lib/finance";
import { localeTag } from "@/lib/format";
import appStyles from "../app.module.css";
import { useAuth, useUser } from "../auth-provider";
import { Button, Loading, Notice } from "../ui";
import s from "./finance.module.css";
import { Select } from "../select";

/**
 * What every money screen needs, loaded once per app visit: setup state, active accounts,
 * categories, and currencies. `revision` goes up after any save, so each page refetches what it
 * shows (totals, lists, balances) without reloading the whole page.
 *
 * Setup (default Cash and Bank accounts, default categories) runs here automatically on the first
 * visit. It's idempotent on the server, so a refresh mid-setup or two tabs at once are harmless.
 * The only question it can ask is a currency, and only when none is saved.
 */
export type FinanceData = {
  setup: SetupState;
  accounts: Account[];
  categories: Category[];
  currencies: CurrencyInfo[];
};

type Load =
  | { kind: "loading" }
  | { kind: "needsCurrency" }
  | { kind: "failed" }
  | ({ kind: "ready" } & FinanceData);

type State = {
  load: Load;
  revision: number;
  /** Call after any write: reloads accounts and categories, and tells pages to refetch. */
  changed: () => Promise<void>;
  quickAdd: {
    open: boolean;
    setOpen: (open: boolean) => void;
    /** What the dialog opens with (a template, or a day from the calendar). */
    preset: QuickPreset | null;
    /** Opens Quick Add with details filled in; the person still reviews and saves. */
    openWith: (preset: QuickPreset | null) => void;
  };
};

/** Details Quick Add starts with. Nothing is saved until the person presses Save. */
export type QuickPreset = {
  n: number;
  kind?: "income" | "expense";
  amount?: string | null;
  currency?: string | null;
  accountId?: string | null;
  categoryId?: string | null;
  description?: string | null;
  date?: string | null;
  templateId?: string | null;
};

const Context = createContext<State | null>(null);

export type Finance = FinanceData & {
  defaultAccountId: string | null;
  revision: number;
  changed: () => Promise<void>;
  quickAdd: State["quickAdd"];
};

/** The loaded data. Only inside <FinanceGate>, which renders nothing until it's ready. */
export function useFinance(): Finance {
  const state = useContext(Context);
  if (!state || state.load.kind !== "ready") {
    throw new Error("useFinance must be used inside a ready <FinanceGate>");
  }
  const { setup, accounts, categories, currencies } = state.load;
  return {
    setup,
    accounts,
    categories,
    currencies,
    defaultAccountId: setup.default_account_id,
    revision: state.revision,
    changed: state.changed,
    quickAdd: state.quickAdd,
  };
}

/** For screens that load their own data: the counter that says "something was saved". */
export function useFinanceRevision(): number {
  return useContext(Context)?.revision ?? 0;
}

/** For the shell: the global Add button needs to know whether Quick Add can open yet. */
export function useFinanceState(): State | null {
  return useContext(Context);
}

async function loadAll(): Promise<Load> {
  let setup = await financeApi.setup();
  if (!setup.completed) {
    if (setup.needs_currency) return { kind: "needsCurrency" };
    setup = await financeApi.runSetup();
  }
  const [accounts, categories, currencies] = await Promise.all([
    accountsApi.list("active"),
    financeApi.categories(),
    accountsApi.currencies(),
  ]);
  return { kind: "ready", setup, accounts, categories, currencies };
}

export function FinanceProvider({ children }: { children: ReactNode }) {
  const { handleAuthError } = useAuth();
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [revision, setRevision] = useState(0);
  const [quickAddOpen, setQuickAddOpen] = useState(false);
  const [preset, setPreset] = useState<QuickPreset | null>(null);
  const openWith = useCallback((next: QuickPreset | null) => {
    setPreset(next);
    setQuickAddOpen(true);
  }, []);

  const fetchAll = useCallback(async (): Promise<Load | null> => {
    try {
      return await loadAll();
    } catch (error) {
      return handleAuthError(error) ? null : { kind: "failed" };
    }
  }, [handleAuthError]);

  useEffect(() => {
    let cancelled = false;
    void fetchAll().then((result) => {
      if (!cancelled && result) setLoad(result);
    });
    return () => {
      cancelled = true;
    };
  }, [fetchAll]);

  const changed = useCallback(async () => {
    const result = await fetchAll();
    if (result) setLoad(result);
    setRevision((n) => n + 1);
  }, [fetchAll]);

  const value = useMemo<State>(
    () => ({
      load,
      revision,
      changed,
      quickAdd: {
        open: quickAddOpen,
        setOpen: (open: boolean) => {
          if (!open) setPreset(null);
          setQuickAddOpen(open);
        },
        preset,
        openWith,
      },
    }),
    [load, revision, changed, quickAddOpen, preset, openWith],
  );

  return <Context.Provider value={value}>{children}</Context.Provider>;
}

/** Renders its children once setup is done and the shared data is loaded. */
export function FinanceGate({ children }: { children: ReactNode }) {
  const state = useContext(Context);
  if (!state || state.load.kind === "loading") return <Loading />;
  if (state.load.kind === "failed") return <Failed onRetry={() => void state.changed()} />;
  if (state.load.kind === "needsCurrency") {
    return <ChooseCurrency onDone={() => void state.changed()} />;
  }
  return <>{children}</>;
}

function Failed({ onRetry }: { onRetry: () => void }) {
  const t = useTranslations("app.home");
  return (
    <Notice tone="error">
      {t("loadFailed")}{" "}
      <button type="button" className={appStyles.linkBtn} onClick={onRetry}>
        {t("retry")}
      </button>
    </Notice>
  );
}

/** The one question a brand-new user may get: which currency to start in. */
function ChooseCurrency({ onDone }: { onDone: () => void }) {
  const t = useTranslations("app.home.welcome");
  const locale = useLocale() as Locale;
  const user = useUser();
  const { handleAuthError } = useAuth();
  const [currencies, setCurrencies] = useState<CurrencyInfo[]>([]);
  const [currency, setCurrency] = useState(locale === "tr" ? "TRY" : "USD");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    accountsApi
      .currencies()
      .then((list) => {
        if (!cancelled) setCurrencies(list);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const names = useMemo(() => {
    const display = new Intl.DisplayNames([localeTag(locale)], { type: "currency" });
    return currencies.map((c) => ({
      code: c.code,
      label: `${c.code} · ${display.of(c.code) ?? c.code}`,
    }));
  }, [currencies, locale]);

  const start = async () => {
    setBusy(true);
    setFailed(false);
    try {
      await financeApi.runSetup(currency);
      onDone();
    } catch (error) {
      if (!handleAuthError(error)) setFailed(true);
    } finally {
      setBusy(false);
    }
  };

  const firstName = user.display_name?.trim().split(/\s+/u)[0];

  return (
    <section className={s.welcome} aria-labelledby="welcome-title">
      <h1 id="welcome-title" className={s.welcomeTitle}>
        {firstName ? t("titleNamed", { name: firstName }) : t("title")}
      </h1>
      <p className={appStyles.muted}>{t("lead")}</p>
      <div className={appStyles.field}>
        <label className={appStyles.label} htmlFor="welcome-currency">
          {t("currency")}
        </label>
        <Select id="welcome-currency" value={currency} onValueChange={setCurrency}>
          {names.length === 0 ? <option value={currency}>{currency}</option> : null}
          {names.map((c) => (
            <option key={c.code} value={c.code}>
              {c.label}
            </option>
          ))}
        </Select>
        <span className={appStyles.hint}>{t("currencyHint")}</span>
      </div>
      {failed ? <Notice tone="error">{t("failed")}</Notice> : null}
      <Button variant="primary" busy={busy} onClick={() => void start()}>
        {t("start")}
      </Button>
    </section>
  );
}
