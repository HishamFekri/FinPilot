import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";
import { forgetLoads } from "@/components/app/load-cache";

afterEach(() => {
  cleanup();
  forgetLoads();
  vi.unstubAllGlobals();
});
