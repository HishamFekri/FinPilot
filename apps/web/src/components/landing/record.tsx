"use client";

import { Select } from "@base-ui/react/select";
import { Bus, Check, ChevronDown, ShoppingBasket, Ticket, Utensils } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { type FormEvent, useEffect, useId, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import { useFormatters } from "./format";
import s from "./landing.module.css";
import { useReveal } from "./motion";

const CATS = [
  { key: "groceries", icon: ShoppingBasket },
  { key: "eating", icon: Utensils },
  { key: "transport", icon: Bus },
  { key: "fun", icon: Ticket },
] as const;
type Cat = (typeof CATS)[number]["key"];
type Account = "bank" | "cash";
type Item = { id: number; amount: number; cat: Cat; account: Account };

const START: Item[] = [
  { id: 2, amount: 2.6, cat: "transport", account: "cash" },
  { id: 1, amount: 3.4, cat: "eating", account: "bank" },
];

function SelectedIcon({ cat }: { cat: Cat }) {
  const Icon = CATS.find((c) => c.key === cat)!.icon;
  return (
    <span className={s.selectItemIcon} aria-hidden="true">
      <Icon />
    </span>
  );
}

/** Reads an amount the way the visitor's language writes it, including Arabic-Indic digits. */
export function parseAmount(raw: string, locale: string): number | null {
  let text = raw.trim().replace(/[٠-٩]/g, (d) => String(d.charCodeAt(0) - 0x0660));
  text = text.replace(/٫/g, ".").replace(/[٬\s  ]/g, "");
  if (locale === "tr") text = text.replace(/\./g, "").replace(",", ".");
  else text = text.replace(/,/g, "");
  if (!/^\d+(\.\d{1,2})?$/.test(text)) return null;
  const value = Number(text);
  return value > 0 && value < 10_000_000 ? value : null;
}

/**
 * A compact, honest demo of recording an expense: amount, category, account, add. Entries live in
 * this component's state only. Nothing is saved or sent anywhere, and the page says so.
 */
export function Record() {
  const t = useTranslations("landing.record");
  const cat = useTranslations("landing.cat");
  const locale = useLocale();
  const f = useFormatters();
  const sectionRef = useRef<HTMLElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const formRef = useRef<HTMLFormElement>(null);
  const nextId = useRef(START.length + 1);
  const ids = useId();
  const [amount, setAmount] = useState("");
  const [category, setCategory] = useState<Cat>("groceries");
  const [account, setAccount] = useState<Account>("bank");
  const [items, setItems] = useState<Item[]>(START);
  const [error, setError] = useState<"empty" | "invalid" | null>(null);
  const [added, setAdded] = useState<Item | null>(null);
  useReveal(sectionRef, 0.25);

  // The confirmation fades on its own; a new entry restarts it.
  useEffect(() => {
    if (!added) return;
    const timer = window.setTimeout(() => setAdded(null), 2600);
    return () => window.clearTimeout(timer);
  }, [added]);

  const parsed = amount.trim() ? parseAmount(amount, locale) : null;

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!amount.trim()) return setError("empty");
    if (parsed === null) return setError("invalid");
    setError(null);
    const item: Item = { id: nextId.current++, amount: parsed, cat: category, account };
    setItems((list) => [item, ...list].slice(0, 3));
    setAdded(item);
    setAmount("");
    inputRef.current?.focus();
  }

  return (
    <section
      data-surface="page"
      ref={sectionRef}
      id="record"
      className={s.record}
      aria-labelledby="record-title"
    >
      <div className={cn(s.wrap, s.recordGrid)}>
        <div>
          <header className={s.sectionHead}>
            <h2 id="record-title">{t("title")}</h2>
            <p className={s.lede}>{t("lede")}</p>
          </header>
          <ul className={s.benefits}>
            {(["one", "two", "three"] as const).map((k) => (
              <li key={k}>
                <span className={s.benefitIcon} aria-hidden="true">
                  <Check />
                </span>
                {t(`points.${k}`)}
              </li>
            ))}
          </ul>
        </div>

        <form
          ref={formRef}
          className={s.quick}
          onSubmit={submit}
          noValidate
          aria-labelledby={`${ids}-h`}
        >
          <div className={s.quickHead}>
            <h3 id={`${ids}-h`}>{t("demo.title")}</h3>
            <span className={s.example}>{t("demo.badge")}</span>
          </div>

          <div className={s.field}>
            <label htmlFor={`${ids}-amount`} className={s.fieldLabel}>
              {t("demo.amount")}
            </label>
            <div className={s.amountWrap}>
              <span className={s.amountCurrency} aria-hidden="true">
                {f.currencySymbol}
              </span>
              <input
                ref={inputRef}
                id={`${ids}-amount`}
                className={s.amountInput}
                inputMode="decimal"
                autoComplete="off"
                placeholder={t("demo.placeholder")}
                value={amount}
                aria-invalid={error ? "true" : undefined}
                aria-describedby={`${ids}-hint`}
                onChange={(e) => {
                  setAmount(e.target.value);
                  if (error) setError(null);
                }}
              />
            </div>
            <p
              id={`${ids}-hint`}
              className={error ? s.fieldError : s.echo}
              role={error ? "alert" : undefined}
            >
              {error
                ? t(`demo.${error}`)
                : parsed !== null
                  ? t.rich("demo.reads", { value: () => <strong>{f.money(parsed)}</strong> })
                  : t("demo.hint")}
            </p>
          </div>

          <div className={s.fieldRow}>
            <div className={s.field}>
              <span id={`${ids}-cat-label`} className={s.fieldLabel}>
                {t("demo.category")}
              </span>
              {/* Base UI Select: a styled listbox, not the operating system's menu. It handles
                  arrow keys, typeahead, Escape, outside clicks, and focus return. */}
              <Select.Root
                value={category}
                onValueChange={(v) => v && setCategory(v as Cat)}
                items={CATS.map(({ key }) => ({ value: key, label: cat(key) }))}
              >
                <Select.Trigger className={s.select} aria-labelledby={`${ids}-cat-label`}>
                  <span className={s.selectValue}>
                    <SelectedIcon cat={category} />
                    <Select.Value />
                  </span>
                  <Select.Icon className={s.selectIcon}>
                    <ChevronDown aria-hidden="true" />
                  </Select.Icon>
                </Select.Trigger>
                <Select.Portal container={formRef}>
                  <Select.Positioner
                    className={s.selectPositioner}
                    alignItemWithTrigger={false}
                    sideOffset={6}
                  >
                    <Select.Popup className={s.selectPopup}>
                      <Select.List>
                        {CATS.map(({ key, icon: Icon }) => (
                          <Select.Item key={key} value={key} className={s.selectItem}>
                            <span className={s.selectItemIcon} aria-hidden="true">
                              <Icon />
                            </span>
                            <Select.ItemText>{cat(key)}</Select.ItemText>
                            <Select.ItemIndicator className={s.selectCheck}>
                              <Check aria-hidden="true" />
                            </Select.ItemIndicator>
                          </Select.Item>
                        ))}
                      </Select.List>
                    </Select.Popup>
                  </Select.Positioner>
                </Select.Portal>
              </Select.Root>
            </div>
            <fieldset className={s.field}>
              <legend className={s.fieldLabel}>{t("demo.account")}</legend>
              <div className={s.toggleGroup}>
                {(["bank", "cash"] as const).map((key) => (
                  <label key={key} className={s.toggleOption}>
                    <input
                      type="radio"
                      name={`${ids}-acct`}
                      value={key}
                      checked={account === key}
                      onChange={() => setAccount(key)}
                    />
                    <span>{t(`demo.${key}`)}</span>
                  </label>
                ))}
              </div>
            </fieldset>
          </div>

          <button type="submit" className={s.addBtn}>
            {t("demo.add")}
          </button>

          <p className={s.added} role="status" data-show={added ? "true" : "false"}>
            {added ? (
              <>
                <span className={s.addedIcon} aria-hidden="true">
                  <Check />
                </span>
                {t("added", { amount: f.money(added.amount), category: cat(added.cat) })}
              </>
            ) : null}
          </p>

          <ul className={s.todayList} aria-label={t("demo.today")}>
            {items.map((item) => {
              const Icon = CATS.find((c) => c.key === item.cat)!.icon;
              return (
                <li key={item.id} className={s.todayItem}>
                  <div className={s.todayRow}>
                    <span className={s.todayIcon} aria-hidden="true">
                      <Icon />
                    </span>
                    <span>
                      {cat(item.cat)}
                      <small className={s.todayMeta}>{t(`demo.${item.account}`)}</small>
                    </span>
                    <span className={s.num}>{f.money(-item.amount)}</span>
                  </div>
                </li>
              );
            })}
          </ul>
          <p className={s.disclaimer}>{t("demo.disclaimer")}</p>
        </form>
      </div>
    </section>
  );
}
