import { describe, expect, it } from "vitest";

import { ApiError } from "./client-api";
import { describeError, formatUtc } from "./messages";

describe("describeError", () => {
  it("maps known API codes to guidance", () => {
    expect(describeError(new ApiError(401, "invalid_credentials"))).toBe("Incorrect username or password.");
    expect(describeError(new ApiError(502, "api_unreachable"))).toMatch(/could not be reached/);
  });

  it("includes the retry delay for lockouts", () => {
    expect(describeError(new ApiError(429, "too_many_failed_attempts", 30))).toBe(
      "Too many failed sign-in attempts. Try again in 30 seconds.",
    );
  });

  it("surfaces validation messages from the API", () => {
    expect(describeError(new ApiError(422, "password must be at least 12 characters"))).toBe(
      "Password must be at least 12 characters.",
    );
  });

  it("never reports unknown failures as success", () => {
    expect(describeError(new ApiError(500, "http_500"))).toBe("The server reported an error (HTTP 500).");
    expect(describeError(new Error("boom"))).toBe("An unexpected error occurred.");
  });
});

describe("formatUtc", () => {
  it("formats timestamps deterministically in UTC", () => {
    expect(formatUtc("2026-09-15T06:05:04Z")).toBe("15 Sept 2026, 06:05:04 UTC");
    expect(formatUtc(null)).toBe("—");
    expect(formatUtc("not a date")).toBe("—");
  });
});
