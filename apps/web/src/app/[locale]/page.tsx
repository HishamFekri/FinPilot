import { setRequestLocale } from "next-intl/server";
import { use } from "react";
import { AuthMessage } from "@/components/landing/auth-message";
import { FinalCta } from "@/components/landing/final";
import { LandingHeader } from "@/components/landing/header";
import { Hero } from "@/components/landing/hero";
import { Journey } from "@/components/landing/journey";
import s from "@/components/landing/landing.module.css";
import { Plan } from "@/components/landing/plan";
import { Record } from "@/components/landing/record";
import { Reports } from "@/components/landing/reports";
import { Stack } from "@/components/landing/stack";
import { Story } from "@/components/landing/story";
import { Trip } from "@/components/landing/trip";
import type { Locale } from "@/i18n/routing";

// The public landing page and the only way in. Every figure on it is illustrative; the only live
// action is "Continue with Google", which uses the real sign-in route.
export default function HomePage({ params }: { params: Promise<{ locale: Locale }> }) {
  setRequestLocale(use(params).locale);

  return (
    <div className={s.root}>
      <LandingHeader />
      <AuthMessage />
      <main>
        <Hero />
        <Journey />
        <Story />
        {/* Record, plan, review: three everyday tools, told as panels that stack. */}
        <Stack>
          <Record />
          <Plan />
          <Reports />
        </Stack>
        <Trip />
        <FinalCta />
      </main>
    </div>
  );
}
