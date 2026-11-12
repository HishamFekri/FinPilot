import { Noto_Naskh_Arabic, Roboto, Rubik, Source_Serif_4, Unbounded } from "next/font/google";

// Self-hosted by next/font. Latin subsets must include Turkish letters (ğ ş ı İ) and ₺.
// Rubik covers Latin and Arabic for body text. Unbounded is the wordmark and landing display face,
// for Latin scripts only; Arabic headings fall back to Rubik (see globals.css).
const body = Rubik({ subsets: ["latin", "latin-ext", "arabic"], variable: "--font-latin" });
const display = Unbounded({
  subsets: ["latin", "latin-ext"],
  weight: ["500", "700", "800"],
  variable: "--font-display",
});

// The app's editorial voice: headings and financial highlights. Source Serif has real tabular
// lining figures, an optical size axis for large numbers, and ₺. Naskh carries Arabic in the same
// register, since Latin digits still come from Source Serif.
const serif = Source_Serif_4({
  subsets: ["latin", "latin-ext"],
  axes: ["opsz"],
  variable: "--font-serif",
});
const serifArabic = Noto_Naskh_Arabic({ subsets: ["arabic"], variable: "--font-serif-ar" });

// Google's sign-in branding guidelines ask for Roboto Medium on the "Sign in with Google" button.
const google = Roboto({
  subsets: ["latin", "latin-ext"],
  weight: ["500"],
  variable: "--font-google",
});

export const fontVariables = `${body.variable} ${display.variable} ${serif.variable} ${serifArabic.variable} ${google.variable}`;
