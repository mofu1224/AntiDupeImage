const EXPLICIT_TIME_ZONE = /(?:Z|[+-]\d{2}:?\d{2})$/i;

export function parseApiUtcTimestamp(value: string): number {
  const normalized = value.trim().replace(" ", "T");
  return Date.parse(EXPLICIT_TIME_ZONE.test(normalized) ? normalized : `${normalized}Z`);
}

export function formatLocalTimestamp(value: string): string {
  const timestamp = parseApiUtcTimestamp(value);
  if (!Number.isFinite(timestamp)) return value;
  const date = new Date(timestamp);
  return [
    `${date.getFullYear()}-${twoDigits(date.getMonth() + 1)}-${twoDigits(date.getDate())}`,
    `${twoDigits(date.getHours())}:${twoDigits(date.getMinutes())}:${twoDigits(date.getSeconds())}`
  ].join(" ");
}

function twoDigits(value: number): string {
  return String(value).padStart(2, "0");
}
