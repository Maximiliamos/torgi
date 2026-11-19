import type { ReliabilitySource } from "./api";

/** Keep transport and parser implementation details in the expanded source row. */
export function sourceProblemMessage(source: ReliabilitySource): string | null {
  const error = source.current_error?.toLocaleLowerCase("ru-RU");
  if (error) {
    if (/cookie|session|unauthorized|http 401|http 403/.test(error)) return "Требуется проверить доступ к источнику";
    if (/429|too many requests/.test(error)) return "Источник ограничил частоту запросов";
    if (/certificate|ssl|tls/.test(error)) return "Не удалось подтвердить безопасное соединение";
    if (/timeout|timed out|connection|dns|resolve/.test(error)) return "Источник недоступен или не ответил вовремя";
    return "Сбой получения или обработки данных. Откройте подробности";
  }
  return source.retry_overdue ? "Повторная попытка просрочена" : null;
}
