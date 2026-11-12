import { useTranslations } from "next-intl";
import s from "./landing.module.css";

const CREATOR_URL = "https://hishamjouda.com";
// The creator's own logo, copied unchanged from the file he supplied. Its transparent padding is
// trimmed by display geometry only (see .creatorLogo), never by editing the image.
const CREATOR_LOGO = "/brand/hisham-jouda-logo.png";

/** "BY / Hisham Jouda": the logo in a dark rounded square, two lines of text, one link. */
export function CreatorCredit() {
  const t = useTranslations("landing.footer");

  return (
    <a className={s.creator} href={CREATOR_URL} target="_blank" rel="noopener noreferrer">
      <span className={s.creatorMark} aria-hidden="true">
        <span className={s.creatorLogo}>
          {/* eslint-disable-next-line @next/next/no-img-element -- a small static mark */}
          <img src={CREATOR_LOGO} alt="" width={1254} height={1254} decoding="async" />
        </span>
      </span>
      <span className={s.creatorText}>
        <span className={s.creatorBy}>{t("createdBy")}</span>
        <span className={s.creatorName}>{t("creator")}</span>
      </span>
    </a>
  );
}
