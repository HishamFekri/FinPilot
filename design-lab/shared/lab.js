/* FinPilot design lab: shared helpers for the three prototypes.
   Illustrative data only. No network calls, no authentication, no real finances. */
(function () {
  "use strict";

  const COMMON = {
    en: {
      "nav.home": "Home", "nav.accounts": "Accounts", "nav.transactions": "Transactions",
      "nav.budgets": "Budgets", "nav.insights": "Insights", "nav.reports": "Reports",
      "nav.trips": "Trips", "nav.settings": "Settings", "nav.more": "More",
      "quickadd": "Quick Add", "google": "Continue with Google", "signin": "Sign in",
      "illustrative": "Illustrative data. Not real finances.",
      "spent": "Spent", "spent.hint": "Your share, trips included",
      "cashout": "Cash out", "cashout.hint": "What left your accounts",
      "income": "Income", "recent": "Recent",
      "trip.owed": "Cappadocia trip: you're owed",
      "try.spent": "Turkish lira spending:",
      "signin.title": "Sign in to FinPilot",
      "signin.body": "Use your Google account. FinPilot never sees your Google password.",
      "signin.note": "No bank connections. You record everything yourself.",
      "signin.redirecting": "Opening Google…",
      "signin.err.cancelled": "Sign-in was cancelled. You can try again.",
      "signin.err.expired": "You were signed out. Sign in again to continue.",
      "signin.legal": "By continuing you agree to the Terms and Privacy notice.",
      "proto.noauth": "Prototype: this button only switches the preview.",
      "menu.signedin": "Signed in as", "menu.profile": "Profile", "menu.sessions": "Sessions",
      "menu.language": "Language", "menu.signout": "Sign out",
      "qa.soon": "Recording arrives with transactions (A3). This is a preview.",
      "qa.amount": "Amount", "qa.reads": "Reads as",
      "cat.groceries": "Groceries", "cat.rent": "Rent", "cat.transport": "Transport",
      "cat.eating": "Eating out", "cat.fun": "Fun", "cat.health": "Health", "cat.other": "Other",
      "tx.migros": "Migros", "tx.metro": "Metro card", "tx.salary": "Salary",
      "tx.cinema": "Cinema", "tx.dinner": "Dinner, Cappadocia", "tx.dinner.note": "Paid {paid}, your share {share}",
      "budget.left": "{amount} left", "budget.over": "{amount} over",
      "chart.table": "Show as table", "day": "Day",
    },
    ar: {
      "nav.home": "الرئيسية", "nav.accounts": "الحسابات", "nav.transactions": "المعاملات",
      "nav.budgets": "الميزانيات", "nav.insights": "التحليلات", "nav.reports": "التقارير",
      "nav.trips": "الرحلات", "nav.settings": "الإعدادات", "nav.more": "المزيد",
      "quickadd": "إضافة سريعة", "google": "المتابعة باستخدام Google", "signin": "تسجيل الدخول",
      "illustrative": "بيانات توضيحية، وليست أموالاً حقيقية.",
      "spent": "الإنفاق", "spent.hint": "حصتك، بما فيها الرحلات",
      "cashout": "المدفوع فعلياً", "cashout.hint": "ما خرج من حساباتك",
      "income": "الدخل", "recent": "الأحدث",
      "trip.owed": "رحلة كابادوكيا: مستحق لك",
      "try.spent": "الإنفاق بالليرة التركية:",
      "signin.title": "سجّل الدخول إلى FinPilot",
      "signin.body": "استخدم حساب Google. لا يرى FinPilot كلمة مرور Google أبداً.",
      "signin.note": "لا ربط بالبنوك. أنت تسجّل كل شيء بنفسك.",
      "signin.redirecting": "جارٍ فتح Google…",
      "signin.err.cancelled": "أُلغي تسجيل الدخول. يمكنك المحاولة مجدداً.",
      "signin.err.expired": "تم تسجيل خروجك. سجّل الدخول مجدداً للمتابعة.",
      "signin.legal": "بالمتابعة، أنت توافق على الشروط وإشعار الخصوصية.",
      "proto.noauth": "نموذج أولي: هذا الزر يبدّل المعاينة فقط.",
      "menu.signedin": "مسجّل الدخول باسم", "menu.profile": "الملف الشخصي", "menu.sessions": "الجلسات",
      "menu.language": "اللغة", "menu.signout": "تسجيل الخروج",
      "qa.soon": "يتوفر التسجيل مع المعاملات (A3). هذه معاينة.",
      "qa.amount": "المبلغ", "qa.reads": "يُقرأ هكذا",
      "cat.groceries": "البقالة", "cat.rent": "الإيجار", "cat.transport": "المواصلات",
      "cat.eating": "المطاعم", "cat.fun": "الترفيه", "cat.health": "الصحة", "cat.other": "أخرى",
      "tx.migros": "ميغروس", "tx.metro": "بطاقة المترو", "tx.salary": "الراتب",
      "tx.cinema": "السينما", "tx.dinner": "عشاء، كابادوكيا", "tx.dinner.note": "دفعت {paid}، وحصتك {share}",
      "budget.left": "متبقٍ {amount}", "budget.over": "تجاوز بمقدار {amount}",
      "chart.table": "عرض كجدول", "day": "اليوم",
    },
    tr: {
      "nav.home": "Ana sayfa", "nav.accounts": "Hesaplar", "nav.transactions": "İşlemler",
      "nav.budgets": "Bütçeler", "nav.insights": "Analizler", "nav.reports": "Raporlar",
      "nav.trips": "Seyahatler", "nav.settings": "Ayarlar", "nav.more": "Diğer",
      "quickadd": "Hızlı ekle", "google": "Google ile devam et", "signin": "Giriş yap",
      "illustrative": "Örnek veriler. Gerçek finansal bilgi değildir.",
      "spent": "Harcama", "spent.hint": "Seyahatler dahil payınız",
      "cashout": "Çıkan nakit", "cashout.hint": "Hesaplarınızdan çıkan",
      "income": "Gelir", "recent": "Son işlemler",
      "trip.owed": "Kapadokya gezisi: alacağınız",
      "try.spent": "Türk lirası harcama:",
      "signin.title": "FinPilot'a giriş yapın",
      "signin.body": "Google hesabınızı kullanın. FinPilot, Google şifrenizi asla görmez.",
      "signin.note": "Banka bağlantısı yok. Her şeyi siz kaydedersiniz.",
      "signin.redirecting": "Google açılıyor…",
      "signin.err.cancelled": "Giriş iptal edildi. Tekrar deneyebilirsiniz.",
      "signin.err.expired": "Oturumunuz kapandı. Devam etmek için tekrar giriş yapın.",
      "signin.legal": "Devam ederek Koşulları ve Gizlilik bildirimini kabul edersiniz.",
      "proto.noauth": "Prototip: bu düğme yalnızca önizlemeyi değiştirir.",
      "menu.signedin": "Giriş yapan", "menu.profile": "Profil", "menu.sessions": "Oturumlar",
      "menu.language": "Dil", "menu.signout": "Çıkış yap",
      "qa.soon": "Kayıt, işlemlerle birlikte gelir (A3). Bu bir önizlemedir.",
      "qa.amount": "Tutar", "qa.reads": "Şöyle okunur",
      "cat.groceries": "Market", "cat.rent": "Kira", "cat.transport": "Ulaşım",
      "cat.eating": "Dışarıda yemek", "cat.fun": "Eğlence", "cat.health": "Sağlık", "cat.other": "Diğer",
      "tx.migros": "Migros", "tx.metro": "Metro kartı", "tx.salary": "Maaş",
      "tx.cinema": "Sinema", "tx.dinner": "Akşam yemeği, Kapadokya", "tx.dinner.note": "Ödenen {paid}, payınız {share}",
      "budget.left": "{amount} kaldı", "budget.over": "{amount} aşıldı",
      "chart.table": "Tablo olarak göster", "day": "Gün",
    },
  };

  // Intl locales. Arabic uses Western digits here; whether to default to Arabic-Indic is open.
  const INTL = { en: "en-IE", ar: "ar-u-nu-latn", tr: "tr-TR" };

  // ---- Illustrative month: October 2026, EUR. Spending and cash out are separate figures.
  const CATEGORIES = [
    { key: "rent", amount: 950.0 },
    { key: "groceries", amount: 412.8 },
    { key: "eating", amount: 188.1 },
    { key: "transport", amount: 96.5 },
    { key: "other", amount: 90.0 },
    { key: "fun", amount: 64.0 },
    { key: "health", amount: 45.0 },
  ];
  const BUDGETS = [
    { key: "groceries", spent: 412.8, limit: 450 },
    { key: "eating", spent: 188.1, limit: 150 },
    { key: "transport", spent: 96.5, limit: 120 },
    { key: "fun", spent: 64.0, limit: 100 },
  ];
  const TOTALS = { income: 3200.0, spent: 1846.4, cashout: 2046.4, owed: 200.0, trySpent: 4120.0, start: 2410.0 };
  const RECENT = [
    { key: "tx.migros", cat: "groceries", day: 31, amount: -42.3 },
    { key: "tx.metro", cat: "transport", day: 30, amount: -20.0 },
    { key: "tx.salary", cat: "income", day: 28, amount: 2900.0 },
    { key: "tx.cinema", cat: "fun", day: 27, amount: -18.0 },
    { key: "tx.dinner", cat: "eating", day: 18, amount: -100.0, paid: 300.0, trip: true },
  ];

  function seeded(seed) {
    return () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
  }
  const DAYS = (() => {
    const rand = seeded(20261031);
    const weights = [];
    for (let d = 1; d <= 31; d++) {
      const weekend = [3, 4, 10, 11, 17, 18, 24, 25, 31].includes(d); // Oct 2026: 3rd is a Saturday
      weights.push((0.35 + rand() * 1.2) * (weekend ? 1.6 : 1));
    }
    const rest = 796.4; // everything except rent (day 1) and the €100 dinner share (day 18)
    const sum = weights.reduce((a, b) => a + b, 0);
    const exp = weights.map((w) => Math.round((w / sum) * rest * 100) / 100);
    const drift = Math.round((rest - exp.reduce((a, b) => a + b, 0)) * 100) / 100;
    exp[30] = Math.round((exp[30] + drift) * 100) / 100;
    exp[0] += 950;
    exp[17] += 100;
    let balance = TOTALS.start;
    return exp.map((e, i) => {
      const day = i + 1;
      const income = day === 28 ? 2900 : day === 14 ? 300 : 0;
      const cashout = e + (day === 18 ? 200 : 0); // paid €300 for dinner, share €100
      balance = Math.round((balance + income - cashout) * 100) / 100;
      return { day, spent: Math.round(e * 100) / 100, income, cashout, balance };
    });
  })();

  // ---- State
  const store = {
    get(k, d) { try { return localStorage.getItem("fp-lab:" + k) ?? d; } catch { return d; } },
    set(k, v) { try { localStorage.setItem("fp-lab:" + k, v); } catch { /* preview only */ } },
  };
  const state = {
    locale: store.get("locale", "en"),
    theme: store.get("theme", "auto"),
    motion: store.get("motion", "full"),
    view: (location.hash || "#landing").slice(1),
    auth: "default",
  };
  let strings = COMMON;
  const listeners = { locale: [], view: [], auth: [] };

  function t(key, vars) {
    let s = (strings[state.locale] && strings[state.locale][key]) ?? strings.en[key] ?? key;
    if (vars) for (const [k, v] of Object.entries(vars)) s = s.replace("{" + k + "}", v);
    return s;
  }
  function money(amount, opts = {}) {
    const cur = opts.currency || "EUR";
    const f = new Intl.NumberFormat(INTL[state.locale], {
      style: "currency", currency: cur, signDisplay: opts.sign ? "exceptZero" : "auto",
      minimumFractionDigits: opts.whole ? 0 : 2, maximumFractionDigits: opts.whole ? 0 : 2,
    });
    return f.format(amount);
  }
  // Parts for components that animate digits: the number stays LTR, the currency follows the page.
  function moneyParts(amount, opts = {}) {
    const f = new Intl.NumberFormat(INTL[state.locale], { style: "currency", currency: opts.currency || "EUR", minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const numeric = new Set(["minusSign", "plusSign", "integer", "group", "decimal", "fraction"]);
    const out = []; let num = null;
    f.formatToParts(amount).forEach((p) => {
      if (numeric.has(p.type)) { if (!num) { num = { type: "number", value: "" }; out.push(num); } num.value += p.value; }
      else if (p.value.trim()) out.push({ type: p.type, value: p.value.trim() });
    });
    return out;
  }
  function number(n, digits = 0) {
    return new Intl.NumberFormat(INTL[state.locale], { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(n);
  }
  function date(day, opts = { day: "numeric", month: "short" }) {
    return new Intl.DateTimeFormat(INTL[state.locale], { ...opts, timeZone: "UTC" }).format(Date.UTC(2026, 9, day));
  }
  function monthLabel() {
    return new Intl.DateTimeFormat(INTL[state.locale], { month: "long", year: "numeric", timeZone: "UTC" }).format(Date.UTC(2026, 9, 1));
  }
  function reduced() {
    return state.motion === "reduce" || matchMedia("(prefers-reduced-motion: reduce)").matches;
  }
  function dark() {
    return state.theme === "dark" || (state.theme === "auto" && matchMedia("(prefers-color-scheme: dark)").matches);
  }

  function applyText(root = document) {
    root.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
    root.querySelectorAll("[data-i18n-label]").forEach((el) => { el.setAttribute("aria-label", t(el.dataset.i18nLabel)); });
    root.querySelectorAll("[data-money]").forEach((el) => {
      el.textContent = money(Number(el.dataset.money), { currency: el.dataset.currency, sign: "sign" in el.dataset, whole: "whole" in el.dataset });
    });
    root.querySelectorAll("[data-month]").forEach((el) => { el.textContent = monthLabel(); });
  }

  function setLocale(locale) {
    state.locale = locale; store.set("locale", locale);
    document.documentElement.lang = locale;
    document.documentElement.dir = locale === "ar" ? "rtl" : "ltr";
    applyText();
    listeners.locale.forEach((fn) => fn(locale));
    syncBar();
  }
  function setTheme(theme) {
    state.theme = theme; store.set("theme", theme);
    document.documentElement.dataset.theme = theme;
    syncBar();
  }
  function setMotion(motion) {
    state.motion = motion; store.set("motion", motion);
    document.documentElement.dataset.motion = motion;
    syncBar();
  }
  function setAuth(auth) {
    state.auth = auth;
    document.documentElement.dataset.auth = auth;
    listeners.auth.forEach((fn) => fn(auth));
    syncBar();
  }
  function setView(view, { push = true } = {}) {
    if (!document.getElementById("view-" + view)) view = "landing";
    const swap = () => {
      document.querySelectorAll("[data-view]").forEach((el) => { el.hidden = el.dataset.view !== view; });
      state.view = view;
      if (push) history.replaceState(null, "", "#" + view);
      window.scrollTo(0, 0);
      listeners.view.forEach((fn) => fn(view));
      syncBar();
    };
    // Shared-element continuity between views, where supported. Instant when motion is reduced.
    if (document.startViewTransition && !reduced() && state.view !== view) {
      document.startViewTransition(swap);
    } else {
      swap();
    }
    const main = document.getElementById("view-" + view);
    if (main) requestAnimationFrame(() => main.querySelector("h1, h2, [data-focus-first]")?.focus?.({ preventScroll: true }));
  }

  // ---- Prototype bar. Tooling, not part of any design. Always LTR English.
  function bar(conceptName) {
    const el = document.createElement("div");
    el.id = "lab-bar"; el.dir = "ltr"; el.lang = "en";
    el.setAttribute("role", "region"); el.setAttribute("aria-label", "Prototype controls");
    const group = (label, name, options) =>
      `<fieldset><legend>${label}</legend>${options.map(([v, l]) =>
        `<button type="button" data-${name}="${v}" aria-pressed="false">${l}</button>`).join("")}</fieldset>`;
    el.innerHTML = `
      <a class="lab-home" href="../index.html">All concepts</a>
      <strong class="lab-name">${conceptName}</strong>
      ${group("View", "set-view", [["landing", "Landing"], ["signin", "Sign-in"], ["app", "App shell"]])}
      ${group("Language", "set-locale", [["en", "EN"], ["ar", "AR"], ["tr", "TR"]])}
      ${group("Theme", "set-theme", [["auto", "Auto"], ["light", "Light"], ["dark", "Dark"]])}
      ${group("Motion", "set-motion", [["full", "Full"], ["reduce", "Reduced"]])}
      <span class="lab-auth">${group("Auth state", "set-auth", [["default", "Default"], ["redirecting", "Redirecting"], ["cancelled", "Error"], ["expired", "Signed out"]])}</span>
      <span class="lab-tag">Illustrative data · prototype</span>`;
    el.addEventListener("click", (e) => {
      const b = e.target.closest("button"); if (!b) return;
      if (b.dataset.setView) setView(b.dataset.setView);
      if (b.dataset.setLocale) setLocale(b.dataset.setLocale);
      if (b.dataset.setTheme) setTheme(b.dataset.setTheme);
      if (b.dataset.setMotion) setMotion(b.dataset.setMotion);
      if (b.dataset.setAuth) setAuth(b.dataset.setAuth);
    });
    document.body.prepend(el);
  }
  function syncBar() {
    const el = document.getElementById("lab-bar"); if (!el) return;
    const map = { setView: state.view, setLocale: state.locale, setTheme: state.theme, setMotion: state.motion, setAuth: state.auth };
    el.querySelectorAll("button").forEach((b) => {
      const k = Object.keys(b.dataset)[0];
      b.setAttribute("aria-pressed", String(map[k] === b.dataset[k]));
    });
    el.querySelector(".lab-auth").hidden = state.view !== "signin";
  }

  // ---- Small shared behaviours
  function wireCommon() {
    document.addEventListener("click", (e) => {
      const go = e.target.closest("[data-go]");
      if (go) { e.preventDefault(); setView(go.dataset.go); }
      const google = e.target.closest("[data-google]");
      if (google) {
        e.preventDefault();
        setAuth("redirecting");
        setTimeout(() => { setAuth("default"); setView("app"); }, reduced() ? 300 : 900);
      }
      // Popovers: one open at a time, Escape and outside click close.
      const trigger = e.target.closest("[aria-controls][aria-haspopup]");
      document.querySelectorAll("[data-popover]").forEach((p) => {
        const t2 = document.querySelector(`[aria-controls="${p.id}"]`);
        if (trigger && trigger === t2) {
          const open = p.hidden;
          p.hidden = !open; t2.setAttribute("aria-expanded", String(open));
          if (open) p.querySelector("button, a, input")?.focus();
        } else if (!p.contains(e.target)) {
          p.hidden = true; t2?.setAttribute("aria-expanded", "false");
        }
      });
    });
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      document.querySelectorAll("[data-popover]").forEach((p) => {
        if (!p.hidden) { p.hidden = true; const t2 = document.querySelector(`[aria-controls="${p.id}"]`); t2?.setAttribute("aria-expanded", "false"); t2?.focus(); }
      });
    });
    // Quick Add amount echo: shows how the typed amount is read in this language.
    document.addEventListener("input", (e) => {
      if (!e.target.matches("[data-qa-input]")) return;
      const raw = e.target.value.trim();
      const out = document.querySelector(e.target.dataset.qaInput);
      const n = parseAmount(raw);
      out.textContent = raw && Number.isFinite(n) ? money(n) : "—";
    });
  }
  function parseAmount(raw) {
    const arabic = "٠١٢٣٤٥٦٧٨٩";
    let s = raw.replace(/[٠-٩]/g, (d) => String(arabic.indexOf(d))).replace(/[٫]/g, ".").replace(/[٬\s]/g, "");
    if (state.locale === "tr") s = s.replace(/\./g, "").replace(",", ".");
    else s = s.replace(/,/g, "");
    return s === "" ? NaN : Number(s);
  }

  // Count a number up to its value once. Instant when motion is reduced.
  function countUp(el, to, opts = {}) {
    const fmt = opts.format || ((v) => money(v, opts));
    if (reduced()) { el.textContent = fmt(to); return; }
    const from = opts.from ?? 0, dur = opts.duration ?? 700, t0 = performance.now();
    const ease = (x) => 1 - Math.pow(1 - x, 4);
    const step = (now) => {
      const p = Math.min(1, (now - t0) / dur);
      el.textContent = fmt(from + (to - from) * ease(p));
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  // Simple line icons (drawn for the lab; production would use lucide-react, already a dependency).
  const ICONS = {
    home: '<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9v11h14V9"/><path d="M10 20v-6h4v6"/>',
    accounts: '<rect x="3" y="6" width="18" height="13" rx="2"/><path d="M3 10h18"/><path d="M16 15h2"/>',
    transactions: '<path d="M4 7h16"/><path d="M4 12h16"/><path d="M4 17h10"/>',
    budgets: '<circle cx="12" cy="12" r="8.5"/><path d="M12 3.5V12l6 6"/>',
    insights: '<path d="M4 20V10"/><path d="M10 20V4"/><path d="M16 20v-7"/><path d="M21 20H3"/>',
    reports: '<path d="M6 3h9l4 4v14H6z"/><path d="M14 3v5h5"/><path d="M9 13h7"/><path d="M9 17h5"/>',
    trips: '<path d="M3 17c4-1 5-6 9-6s5 5 9 4"/><circle cx="6" cy="8" r="2.5"/><path d="M6 10.5v3"/><circle cx="18" cy="6" r="1.5"/>',
    settings: '<circle cx="12" cy="12" r="3"/><path d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M5.3 5.3l2.1 2.1M16.6 16.6l2.1 2.1M5.3 18.7l2.1-2.1M16.6 7.4l2.1-2.1"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    more: '<circle cx="5" cy="12" r="1.3"/><circle cx="12" cy="12" r="1.3"/><circle cx="19" cy="12" r="1.3"/>',
    user: '<circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6"/>',
    sessions: '<rect x="3" y="4" width="18" height="12" rx="1.5"/><path d="M8 20h8M12 16v4"/>',
    globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3c2.5 3 2.5 15 0 18M12 3c-2.5 3-2.5 15 0 18"/>',
    signout: '<path d="M10 4H5v16h5"/><path d="M15 8l4 4-4 4"/><path d="M19 12H9"/>',
    table: '<rect x="3" y="4" width="18" height="16" rx="1.5"/><path d="M3 10h18M3 15h18M9 4v16"/>',
  };
  function sprite() {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("aria-hidden", "true"); svg.style.display = "none";
    svg.innerHTML = Object.entries(ICONS).map(([k, p]) =>
      `<symbol id="i-${k}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round">${p}</symbol>`).join("");
    document.body.prepend(svg);
  }

  function init({ name, strings: extra = {}, onLocale, onView, onAuth }) {
    sprite();
    strings = { en: { ...COMMON.en, ...extra.en }, ar: { ...COMMON.ar, ...extra.ar }, tr: { ...COMMON.tr, ...extra.tr } };
    if (onLocale) listeners.locale.push(onLocale);
    if (onView) listeners.view.push(onView);
    if (onAuth) listeners.auth.push(onAuth);
    bar(name);
    wireCommon();
    setTheme(state.theme);
    setMotion(state.motion);
    setLocale(state.locale);
    setAuth("default");
    setView(state.view, { push: false });
    addEventListener("hashchange", () => { const v = location.hash.slice(1); if (v && v !== state.view) setView(v, { push: false }); });
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => listeners.locale.forEach((fn) => fn(state.locale)));
  }

  window.LAB = { init, t, money, moneyParts, number, date, monthLabel, reduced, dark, countUp, applyText, setView,
    get locale() { return state.locale; }, get view() { return state.view; },
    DAYS, CATEGORIES, BUDGETS, TOTALS, RECENT };
})();
