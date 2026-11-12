"use client";

import { Check, Info } from "lucide-react";
import { useTranslations } from "next-intl";
import { type CSSProperties, useCallback, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import { type PersonId, TRIP_EXPENSES, TRIP_PEOPLE, TRIP_SETTLEMENTS, tripNets } from "./data";
import { useFormatters } from "./format";
import s from "./landing.module.css";
import { useReveal, useScrollProgress } from "./motion";

// Scroll progress at which each step begins: people, three expenses, balances, settlement.
const STEP_AT = [0, 0.14, 0.3, 0.46, 0.62, 0.8];
type Step = 0 | 1 | 2 | 3 | 4 | 5;

const NETS = tripNets();
const MAX_NET = Math.max(...Object.values(NETS).map(Math.abs));
const TONE = { tang: s.toneTang, sky: s.toneSky, ink: s.toneInk };
const person = (id: PersonId) => TRIP_PEOPLE.find((p) => p.id === id)!;

export function Trip() {
  const t = useTranslations("landing.trip");
  const f = useFormatters();
  const name = (id: PersonId) => t(`names.${id}`);
  const ref = useRef<HTMLElement>(null);
  const [step, setStep] = useState(0);
  useReveal(ref, 0.05);

  const onProgress = useCallback((p: number) => {
    let next = 0;
    STEP_AT.forEach((at, i) => {
      if (p >= at) next = i;
    });
    setStep(next);
  }, []);
  useScrollProgress(ref, onProgress);

  const shown = TRIP_EXPENSES.filter((_, i) => step >= i + 1);
  const total = shown.reduce((sum, e) => sum + e.amount, 0);
  const current = step >= 1 && step <= 3 ? TRIP_EXPENSES[step - 1] : undefined;
  const panel = step <= 3 ? 0 : step === 4 ? 1 : 2;
  const panelState = (i: number) => ({
    "data-active": String(panel === i),
    "data-past": String(panel > i),
  });

  return (
    <section
      data-surface="field"
      ref={ref}
      id="trips"
      className={s.trip}
      aria-labelledby="trip-title"
    >
      <div className={cn(s.wrap, s.tripSticky)}>
        <div className={s.tripGrid}>
          <div className={s.tripHead}>
            <h2 id="trip-title">{t("title")}</h2>
            <p className={s.lede}>{t("lede")}</p>
            <div className={s.captions} aria-live="polite">
              {STEP_AT.map((_, i) => (
                <div
                  key={i}
                  className={s.caption}
                  data-active={String(step === i)}
                  aria-hidden={step !== i}
                >
                  <h3>{t(`steps.${i as Step}.title`)}</h3>
                  <p>{t(`steps.${i as Step}.body`)}</p>
                </div>
              ))}
            </div>
            <ol className={s.progressRail} aria-hidden="true">
              {STEP_AT.map((_, i) => (
                <li key={i} style={{ "--on": step >= i ? 1 : 0 } as CSSProperties} />
              ))}
            </ol>
            <p className={s.tripNote}>
              <Info aria-hidden="true" />
              <span>{t("disclaimer")}</span>
            </p>
            <p className={s.example}>{t("example")}</p>
          </div>

          <div className={s.scene}>
            <ul className={s.people} aria-label={t("people")}>
              {TRIP_PEOPLE.map((p) => {
                const net = NETS[p.id];
                return (
                  <li
                    key={p.id}
                    className={s.person}
                    data-paying={String(current?.paidBy === p.id)}
                  >
                    <span className={cn(s.avatar, TONE[p.tone])} aria-hidden="true">
                      {name(p.id).charAt(0)}
                    </span>
                    <span>{name(p.id)}</span>
                    <span
                      className={cn(s.personNet, s.num, net >= 0 ? s.netPos : s.netNeg)}
                      style={{ opacity: step >= 4 ? 1 : 0 }}
                    >
                      {f.money(net, { whole: true, sign: true })}
                    </span>
                  </li>
                );
              })}
            </ul>

            <div className={s.stageArea}>
              <div className={s.stagePanel} {...panelState(0)}>
                {TRIP_EXPENSES.map((e, i) => (
                  <div
                    key={e.key}
                    className={s.expense}
                    data-shown={String(step >= i + 1)}
                    data-current={String(step === i + 1)}
                  >
                    <span className={cn(s.avatar, TONE[person(e.paidBy).tone])} aria-hidden="true">
                      {name(person(e.paidBy).id).charAt(0)}
                    </span>
                    <span className={s.expenseTitle}>{t(`expenses.${e.key}`)}</span>
                    <span className={cn(s.expenseAmount, s.num)}>
                      {f.money(e.amount, { whole: true })}
                    </span>
                    <span className={s.expenseMeta}>
                      {t("paidBy", {
                        name: name(person(e.paidBy).id),
                        share: f.money(e.amount / 3, { whole: true }),
                      })}
                    </span>
                  </div>
                ))}
                <p className={s.tripTotals}>
                  <span>
                    {t("total")}{" "}
                    <strong className={s.num}>{f.money(total, { whole: true })}</strong>
                  </span>
                  <span>
                    {t("share")}{" "}
                    <strong className={s.num}>{f.money(total / 3, { whole: true })}</strong>
                  </span>
                </p>
              </div>

              <div className={s.stagePanel} {...panelState(1)}>
                <div className={s.balances}>
                  <p className={s.balanceAxis} aria-hidden="true">
                    <span>{t("behind")}</span>
                    <span>{t("ahead")}</span>
                  </p>
                  {TRIP_PEOPLE.map((p) => {
                    const net = NETS[p.id];
                    const w = Math.abs(net) / MAX_NET;
                    return (
                      <div key={p.id} className={s.balance}>
                        <span className={s.balanceName}>{name(p.id)}</span>
                        <span className={cn(s.half, s.halfNeg)}>
                          {net < 0 && (
                            <>
                              <span className={cn(s.balanceValue, s.num, s.netNeg)}>
                                {f.money(net, { whole: true })}
                              </span>
                              <i style={{ "--w": String(w) } as CSSProperties} />
                            </>
                          )}
                        </span>
                        <span className={cn(s.half, s.halfPos)}>
                          {net > 0 && (
                            <>
                              <i style={{ "--w": String(w) } as CSSProperties} />
                              <span className={cn(s.balanceValue, s.num, s.netPos)}>
                                {f.money(net, { whole: true, sign: true })}
                              </span>
                            </>
                          )}
                        </span>
                      </div>
                    );
                  })}
                  <p className={s.expenseMeta}>
                    {t("balanceNote", { share: f.money(300, { whole: true }) })}
                  </p>
                </div>
              </div>

              <div className={s.stagePanel} {...panelState(2)}>
                <ul className={s.transfers}>
                  {TRIP_SETTLEMENTS.map((x, i) => (
                    <li key={x.to} className={s.transfer}>
                      <span className={cn(s.avatar, TONE[person(x.from).tone])} aria-hidden="true">
                        {name(person(x.from).id).charAt(0)}
                      </span>
                      <span
                        className={s.transferLine}
                        style={{ "--d": `${i * 260}ms` } as CSSProperties}
                      >
                        <i />
                        <b className={s.num}>{f.money(x.amount, { whole: true })}</b>
                      </span>
                      <span className={cn(s.avatar, TONE[person(x.to).tone])} aria-hidden="true">
                        {name(person(x.to).id).charAt(0)}
                      </span>
                      <span className={s.srOnly}>
                        {t("pays", {
                          from: name(person(x.from).id),
                          to: name(person(x.to).id),
                          amount: f.money(x.amount),
                        })}
                      </span>
                    </li>
                  ))}
                </ul>
                <p className={s.settled}>
                  <i aria-hidden="true">
                    <Check />
                  </i>
                  {t("settled", { count: TRIP_SETTLEMENTS.length })}
                </p>
                <p className={s.expenseMeta}>{t("confirm")}</p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
