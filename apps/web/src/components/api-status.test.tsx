import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi } from "vitest";
import ar from "../../messages/ar.json";
import en from "../../messages/en.json";
import { ApiStatus } from "./api-status";

function renderStatus(locale: "en" | "ar" = "en") {
  return render(
    <NextIntlClientProvider locale={locale} messages={locale === "en" ? en : ar}>
      <ApiStatus />
    </NextIntlClientProvider>,
  );
}

describe("ApiStatus", () => {
  it("calls the versioned, same-origin health endpoint and reports success", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    renderStatus();

    expect(screen.getByRole("status")).toHaveTextContent(en.status.checking);
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(en.status.ok));
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/v1/healthz");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("reports an error response as unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("", { status: 503 })));
    renderStatus();
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(en.status.unreachable),
    );
  });

  it("offers a retry after a network failure, and the retry really runs", async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("network"))
      .mockResolvedValueOnce(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    renderStatus();

    fireEvent.click(await screen.findByRole("button", { name: en.status.retry }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(en.status.ok));
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("renders translated text", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 200 })));
    renderStatus("ar");
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(ar.status.ok));
  });
});
