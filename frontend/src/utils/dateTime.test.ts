import { describe, expect, test } from "vitest";
import { formatLocalTimestamp, parseApiUtcTimestamp } from "./dateTime";

describe("API timestamps", () => {
  test("treats SQLite timestamps without a suffix as UTC", () => {
    expect(parseApiUtcTimestamp("2026-06-12 00:00:00")).toBe(
      Date.UTC(2026, 5, 12, 0, 0, 0)
    );
  });

  test("preserves timestamps that already include a time zone", () => {
    expect(parseApiUtcTimestamp("2026-06-12T09:00:00+09:00")).toBe(
      Date.UTC(2026, 5, 12, 0, 0, 0)
    );
  });

  test("formats UTC timestamps in the user's local time", () => {
    const date = new Date(Date.UTC(2026, 5, 12, 0, 0, 0));
    const expected = [
      `${date.getFullYear()}-${twoDigits(date.getMonth() + 1)}-${twoDigits(date.getDate())}`,
      `${twoDigits(date.getHours())}:${twoDigits(date.getMinutes())}:${twoDigits(date.getSeconds())}`
    ].join(" ");
    expect(formatLocalTimestamp("2026-06-12 00:00:00")).toBe(expected);
  });
});

function twoDigits(value: number): string {
  return String(value).padStart(2, "0");
}
