/** Date strings from database ORM are naive UTC; never interpret them in the user's browser zone. */
export function parseReliabilityTimestamp(raw: string | null | undefined): Date | null {
  if (!raw) return null;
  const normalized = raw.trim().replace(" ", "T");
  const iso = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$/.test(normalized)
    ? normalized + "Z"
    : normalized;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function timestampEpoch(raw: string | null | undefined): number {
  return parseReliabilityTimestamp(raw)?.getTime() ?? 0;
}

export function moscowTimestamp(raw: string | null | undefined): string {
  const date = parseReliabilityTimestamp(raw);
  if (!date) return "Нет данных";
  return date.toLocaleString("ru-RU", {
    timeZone: "Europe/Moscow",
    day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  }) + " МСК";
}

export function relativeTimestamp(raw: string | null | undefined, now = Date.now()): string {
  const parsed = parseReliabilityTimestamp(raw);
  if (!parsed) return "Нет проверки";
  const seconds = Math.max(0, Math.round((now - parsed.getTime()) / 1000));
  if (seconds < 60) return seconds + " сек назад";
  if (seconds < 3600) return Math.floor(seconds / 60) + " мин назад";
  if (seconds < 86400) return Math.floor(seconds / 3600) + " ч назад";
  return Math.floor(seconds / 86400) + " дн назад";
}
