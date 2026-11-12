/**
 * The web app's Content Security Policy (SECURITY.md §6).
 *
 * Scripts: only those carrying this response's nonce, and what they load ('strict-dynamic').
 * Next.js puts the nonce on its own scripts during rendering; next-themes gets it as a prop.
 * No 'unsafe-inline' for scripts, ever, and 'unsafe-eval' only in development, where React needs
 * it for error stacks.
 *
 * Styles: <style> and <link> elements need the nonce or this origin. Inline style *attributes*
 * are allowed ('style-src-attr'), because React renders style props as attributes and
 * positioning libraries depend on them. An attribute can't run script, so this is the documented
 * exception.
 */
export function buildCsp(nonce: string, { dev }: { dev: boolean }): string {
  const directives: Record<string, string[]> = {
    "default-src": ["'self'"],
    "script-src": [
      "'self'",
      `'nonce-${nonce}'`,
      "'strict-dynamic'",
      ...(dev ? ["'unsafe-eval'"] : []),
    ],
    // Development's error overlay injects unnonced styles; production has no overlay.
    "style-src": ["'self'", dev ? "'unsafe-inline'" : `'nonce-${nonce}'`],
    "style-src-attr": ["'unsafe-inline'"],
    // Google profile pictures (the `picture` claim), loaded with no referrer.
    "img-src": ["'self'", "data:", "blob:", "https://*.googleusercontent.com"],
    "font-src": ["'self'"],
    // Only this origin: the API is reached through /api/v1. Development adds the reload socket.
    "connect-src": ["'self'", ...(dev ? ["ws:"] : [])],
    "object-src": ["'none'"],
    "base-uri": ["'self'"],
    "form-action": ["'self'"],
    "frame-src": ["'none'"],
    "frame-ancestors": ["'none'"],
    "manifest-src": ["'self'"],
    "worker-src": ["'self'", "blob:"],
  };
  const policy = Object.entries(directives).map(([name, values]) => `${name} ${values.join(" ")}`);
  if (!dev) policy.push("upgrade-insecure-requests");
  return policy.join("; ");
}

/** 128 random bits, base64. New for every response. */
export function createNonce(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}
