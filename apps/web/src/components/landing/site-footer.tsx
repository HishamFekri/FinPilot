import { useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";
import { cn } from "@/lib/utils";
import { CreatorCredit } from "./creator";
import s from "./landing.module.css";

const GITHUB_URL = "https://github.com/HishamFekri";
const LINKEDIN_URL = "https://www.linkedin.com/in/hishamjouda";

function GitHubMark() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path
        fill="currentColor"
        d="M12 .3a12 12 0 0 0-3.8 23.38c.6.11.82-.26.82-.58v-2.03c-3.34.73-4.04-1.6-4.04-1.6-.55-1.4-1.34-1.77-1.34-1.77-1.09-.74.08-.73.08-.73 1.2.09 1.84 1.24 1.84 1.24 1.07 1.83 2.8 1.3 3.49 1 .11-.78.42-1.31.76-1.61-2.66-.3-5.47-1.33-5.47-5.93 0-1.31.47-2.38 1.24-3.22-.14-.3-.54-1.52.1-3.18 0 0 1.01-.32 3.3 1.23a11.5 11.5 0 0 1 6 0c2.28-1.55 3.29-1.23 3.29-1.23.64 1.66.24 2.88.12 3.18a4.65 4.65 0 0 1 1.23 3.22c0 4.61-2.8 5.62-5.48 5.92.43.37.81 1.1.81 2.22v3.29c0 .32.22.7.82.58A12 12 0 0 0 12 .3"
      />
    </svg>
  );
}

function LinkedInMark() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="5" cy="4.6" r="2.3" fill="currentColor" />
      <rect x="3" y="8.4" width="4" height="12.6" rx="0.6" fill="currentColor" />
      <path
        fill="currentColor"
        d="M9.4 8.4h3.8v1.75c.58-1.06 1.98-2.05 3.9-2.05 3.62 0 4.3 2.35 4.3 5.45V21h-4v-6.3c0-1.5-.03-3.34-2.03-3.34-2.04 0-2.33 1.58-2.33 3.23V21H9.4z"
      />
    </svg>
  );
}

/** The page's last word, on the orange: the creator's signature, the copyright, and their profiles. */
export function SiteFooter() {
  const t = useTranslations("landing.footer");

  return (
    <footer className={cn(s.wrap, s.siteFooter)}>
      <CreatorCredit />
      <p className={s.copyright}>
        {t("rights", { year: 2026 })}
        {" · "}
        <Link href="/privacy" className="underline underline-offset-4">
          {t("privacy")}
        </Link>
      </p>
      <div className={s.socials}>
        <a
          className={s.social}
          href={GITHUB_URL}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={t("github")}
        >
          <GitHubMark />
        </a>
        <a
          className={cn(s.social, s.socialLinkedIn)}
          href={LINKEDIN_URL}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={t("linkedin")}
        >
          <LinkedInMark />
        </a>
      </div>
    </footer>
  );
}
