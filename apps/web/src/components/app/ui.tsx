import { CircleAlert, CircleCheck, Info } from "lucide-react";
import { useTranslations } from "next-intl";
import { type ButtonHTMLAttributes, type CSSProperties, type ReactNode, useState } from "react";
import { initials } from "@/lib/auth";
import { cn } from "@/lib/utils";
import s from "./app.module.css";

type Variant = "primary" | "secondary" | "ghost" | "danger";
const VARIANTS: Record<Variant, string | undefined> = {
  primary: s.btnPrimary,
  secondary: s.btnSecondary,
  ghost: s.btnGhost,
  danger: s.btnDanger,
};

export function buttonClass(variant: Variant = "secondary", size: "md" | "sm" = "md") {
  return cn(s.btn, VARIANTS[variant], size === "sm" && s.btnSm);
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  size?: "md" | "sm";
  /** Shows a spinner, keeps the label, and blocks further clicks. */
  busy?: boolean;
};

export function Button({
  variant = "secondary",
  size = "md",
  busy = false,
  className,
  children,
  disabled,
  type = "button",
  ...props
}: ButtonProps) {
  return (
    <button
      type={type}
      className={cn(buttonClass(variant, size), className)}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      {...props}
    >
      {busy ? <Spinner /> : null}
      {children}
    </button>
  );
}

export function Spinner() {
  return <span className={s.spinner} aria-hidden="true" />;
}

/**
 * Content on its way. Holds roughly the space the content will take (`lines` rows), so nothing
 * below it jumps when it arrives, and fades in only after a short delay, so a quick load shows
 * nothing at all rather than a flash.
 */
export function Loading({
  label,
  lines = 3,
  className,
}: {
  label?: ReactNode;
  lines?: number;
  className?: string;
}) {
  const t = useTranslations("app.home");
  return (
    <div
      className={cn(s.loading, className)}
      role="status"
      style={{ "--lines": lines } as CSSProperties}
    >
      <span className={s.loadingLabel}>
        <Spinner /> <span>{label ?? t("loading")}</span>
      </span>
      <span className={s.loadingRows} aria-hidden="true">
        {Array.from({ length: lines }, (_, i) => (
          <span key={i} className={s.skel} />
        ))}
      </span>
    </div>
  );
}

const NOTICE = {
  error: { className: s.noticeError, Icon: CircleAlert },
  success: { className: s.noticeSuccess, Icon: CircleCheck },
  info: { className: s.noticeInfo, Icon: Info },
} as const;

/** A short message. Errors are announced at once; others politely. */
export function Notice({
  tone = "info",
  children,
  className,
}: {
  tone?: keyof typeof NOTICE;
  children: ReactNode;
  className?: string;
}) {
  const { className: toneClass, Icon } = NOTICE[tone];
  return (
    <div
      className={cn(s.notice, toneClass, className)}
      role={tone === "error" ? "alert" : "status"}
    >
      <Icon aria-hidden="true" />
      <div>{children}</div>
    </div>
  );
}

export function Tag({
  tone = "neutral",
  children,
}: {
  tone?: "neutral" | "accent" | "positive";
  children: ReactNode;
}) {
  return (
    <span className={cn(s.tag, tone === "accent" && s.tagAccent, tone === "positive" && s.tagPos)}>
      {children}
    </span>
  );
}

/** For rich messages: a user-supplied or formatted value in its own direction run. */
export const bdi = (chunks: ReactNode) => <bdi>{chunks}</bdi>;

/**
 * The person's Google picture when there is one, and their initials otherwise, or if the picture
 * fails to load: Google's picture URLs aren't permanent. The image is decorative (the name is
 * always shown or announced beside it), and no referrer is sent with it.
 */
export function Avatar({
  name,
  email,
  src = null,
  large = false,
}: {
  name: string | null;
  email: string | null;
  src?: string | null;
  large?: boolean;
}) {
  // Remembers which URL failed, so a new picture gets its own chance to load.
  const [failed, setFailed] = useState<string | null>(null);
  const showImage = Boolean(src) && failed !== src;
  return (
    <span className={cn(s.avatar, large && s.avatarLg)} aria-hidden="true" data-avatar>
      {showImage ? (
        // A plain <img>: next/image would fetch the picture through this server.
        // eslint-disable-next-line @next/next/no-img-element
        <img
          className={s.avatarImg}
          src={src ?? undefined}
          alt=""
          referrerPolicy="no-referrer"
          decoding="async"
          draggable={false}
          onError={() => setFailed(src)}
        />
      ) : (
        initials(name, email)
      )}
    </span>
  );
}
