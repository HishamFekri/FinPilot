import { describe, expect, it } from "vitest";
import { resolveApiProxyTarget } from "./api-proxy-target";

describe("resolveApiProxyTarget", () => {
  it("defaults to the local backend", () => {
    expect(resolveApiProxyTarget(undefined)).toBe("http://127.0.0.1:8000");
  });

  it("reduces a URL to its origin", () => {
    expect(resolveApiProxyTarget("https://api.example.test:8443/ignored")).toBe(
      "https://api.example.test:8443",
    );
  });

  it("rejects malformed values and other protocols", () => {
    expect(() => resolveApiProxyTarget("not a url")).toThrow();
    expect(() => resolveApiProxyTarget("file:///etc/passwd")).toThrow(/http or https/);
  });
});
