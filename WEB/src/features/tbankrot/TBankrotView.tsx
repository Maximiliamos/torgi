import React from "react";
import { ArrowLeft, CheckCircle2, KeyRound, Loader2, RefreshCcw, ShieldAlert, ShieldCheck, X } from "lucide-react";

import {
  ApiError,
  closeTBankrotAuth,
  controlTBankrotAuth,
  fetchTBankrotFrame,
  fetchTBankrotStatus,
  startTBankrotAuth,
  syncTBankrot,
  TBankrotBrowserSession,
  TBankrotStatus,
  verifyTBankrotAuth,
} from "../../lib/api";

function stateLabel(status: TBankrotStatus | null) {
  if (!status) return "Проверяем";
  if (status.state === "ready") return "Авторизация активна";
  if (status.state === "syncing") return "Синхронизация";
  if (status.state === "auth_required") return "Требуется авторизация";
  return "Browser broker недоступен";
}

function dateTime(value?: string | null) {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
  }).format(parsed);
}

export function TBankrotView({
  refreshToken,
  isAdmin,
}: {
  refreshToken: number;
  isAdmin: boolean;
}) {
  const [status, setStatus] = React.useState<TBankrotStatus | null>(null);
  const [session, setSession] = React.useState<TBankrotBrowserSession | null>(null);
  const [frameUrl, setFrameUrl] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState("");
  const [message, setMessage] = React.useState("");
  const [frameBusy, setFrameBusy] = React.useState(false);
  const actionQueue = React.useRef<Promise<unknown>>(Promise.resolve());

  const loadStatus = React.useCallback(async () => {
    try {
      setStatus(await fetchTBankrotStatus());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось проверить TBankrot");
    }
  }, []);

  React.useEffect(() => {
    void loadStatus();
    const timer = window.setInterval(() => void loadStatus(), 7000);
    return () => window.clearInterval(timer);
  }, [loadStatus, refreshToken]);

  const refreshFrame = React.useCallback(async () => {
    if (!session || frameBusy) return;
    setFrameBusy(true);
    try {
      const blob = await fetchTBankrotFrame(session.session_id);
      const next = URL.createObjectURL(blob);
      setFrameUrl((previous) => {
        if (previous) URL.revokeObjectURL(previous);
        return next;
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось получить экран TBankrot");
    } finally {
      setFrameBusy(false);
    }
  }, [session, frameBusy]);

  React.useEffect(() => {
    if (!session) return;
    void refreshFrame();
    const timer = window.setInterval(() => void refreshFrame(), 1400);
    return () => window.clearInterval(timer);
  }, [session, refreshFrame]);

  React.useEffect(() => () => {
    if (frameUrl) URL.revokeObjectURL(frameUrl);
  }, [frameUrl]);

  const sendAction = React.useCallback((action: {
    type: string;
    x?: number;
    y?: number;
    value?: string;
    key?: string;
    delta_y?: number;
  }) => {
    if (!session) return;
    actionQueue.current = actionQueue.current
      .catch(() => undefined)
      .then(() => controlTBankrotAuth(session.session_id, action))
      .then(() => refreshFrame())
      .catch((err) => setError(err instanceof Error ? err.message : "Ошибка управления браузером"));
  }, [session, refreshFrame]);

  const startAuth = async () => {
    setBusy(true); setError(""); setMessage("");
    try {
      const value = await startTBankrotAuth();
      setSession(value);
      setMessage("Откройте форму входа TBankrot на экране ниже. Логин, пароль и CAPTCHA вводите только в этом окне.");
      window.setTimeout(() => void refreshFrame(), 100);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось открыть TBankrot");
    } finally {
      setBusy(false);
    }
  };

  const closeAuth = async () => {
    if (!session) return;
    setBusy(true);
    try {
      await closeTBankrotAuth(session.session_id);
    } catch {
      // Session is disposable; local UI can still close if broker already expired it.
    } finally {
      setSession(null);
      setFrameUrl((previous) => {
        if (previous) URL.revokeObjectURL(previous);
        return "";
      });
      setBusy(false);
      void loadStatus();
    }
  };

  const verify = async () => {
    if (!session) return;
    setBusy(true); setError(""); setMessage("Проверяем доступ к полному списку лотов…");
    try {
      const result = await verifyTBankrotAuth(session.session_id);
      if (!result.ok) {
        setMessage("TBankrot всё ещё ограничивает список. Завершите вход или CAPTCHA и повторите проверку.");
        return;
      }
      setMessage(
        result.sync
          ? "Авторизация подтверждена. Cookies защищённо сохранены, TBankrot sync запущен автоматически."
          : "Авторизация подтверждена и cookies сохранены.",
      );
      await closeTBankrotAuth(session.session_id).catch(() => undefined);
      setSession(null);
      setFrameUrl((previous) => {
        if (previous) URL.revokeObjectURL(previous);
        return "";
      });
      await loadStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось проверить авторизацию");
    } finally {
      setBusy(false);
    }
  };

  const manualSync = async () => {
    setBusy(true); setError(""); setMessage("");
    try {
      const result = await syncTBankrot();
      setMessage(result.status === "already_running"
        ? "Синхронизация уже выполняется."
        : "Изолированная синхронизация TBankrot поставлена в очередь.");
      await loadStatus();
    } catch (err) {
      if (err instanceof ApiError && err.status === 428) {
        setMessage("Сессия TBankrot истекла. Пройдите авторизацию заново.");
        await loadStatus();
      } else {
        setError(err instanceof Error ? err.message : "Не удалось запустить TBankrot");
      }
    } finally {
      setBusy(false);
    }
  };

  const sync = status?.latest_sync;
  const statusOk = status?.state === "ready" || status?.state === "syncing";

  return <section className="tbankrotPage">
    <div className="tbankrotStatusCard">
      <div className="tbankrotStatusIcon" data-ok={statusOk}>
        {statusOk ? <ShieldCheck /> : <ShieldAlert />}
      </div>
      <div>
        <span className="eyebrow">Изолированный источник</span>
        <h2>TBankrot</h2>
        <strong>{stateLabel(status)}</strong>
        <p>
          TBankrot не участвует в автоматическом обновлении основных 4 источников.
          Сбой авторизации здесь не останавливает production reconciliation.
        </p>
      </div>
      <div className="tbankrotStatusMeta">
        <span>Сессия сохранена</span><b>{status?.saved_session ? "Да" : "Нет"}</b>
        <span>Последний вход</span><b>{dateTime(status?.captured_at)}</b>
        <span>Browser broker</span><b>{status?.broker_available ? "Готов" : "Недоступен"}</b>
      </div>
    </div>

    {status?.broker_error && <div className="errorBox"><ShieldAlert size={18} /><span>{status.broker_error}</span></div>}
    {error && <div className="errorBox"><ShieldAlert size={18} /><span>{error}</span></div>}
    {message && <div className="successBox"><CheckCircle2 size={16} />{message}</div>}

    <section className="tbankrotSyncCard">
      <div>
        <h3>Последняя синхронизация</h3>
        {sync ? <>
          <strong>{sync.status}</strong>
          <span>{sync.items_seen} лотов просмотрено · новых {sync.items_inserted} · обновлено {sync.items_updated} · архивировано {sync.items_archived}</span>
          <small>{dateTime(sync.started_at)} → {dateTime(sync.finished_at)}</small>
          {sync.error && <small className="tbankrotWarning">{sync.error}</small>}
        </> : <span>Изолированная синхронизация ещё не запускалась.</span>}
      </div>
      {isAdmin && status?.state === "ready" && !session && (
        <button className="primaryButton" disabled={busy} onClick={() => void manualSync()}>
          {busy ? <Loader2 className="spin" size={16} /> : <RefreshCcw size={16} />}
          Обновить TBankrot сейчас
        </button>
      )}
      {isAdmin && status?.state === "auth_required" && !session && (
        <button className="primaryButton" disabled={busy || !status.broker_available} onClick={() => void startAuth()}>
          {busy ? <Loader2 className="spin" size={16} /> : <KeyRound size={16} />}
          Войти в TBankrot
        </button>
      )}
      {!isAdmin && <small>Повторную авторизацию TBankrot может выполнять только администратор STERDEZ.</small>}
    </section>

    {session && <section className="tbankrotBrowserCard">
      <header>
        <div>
          <span className="eyebrow">Управляемая браузерная сессия</span>
          <strong>{session.title || "TBankrot"}</strong>
          <small>{session.url}</small>
        </div>
        <div>
          <button type="button" onClick={() => sendAction({ type: "back" })} title="Назад"><ArrowLeft size={16} /></button>
          <button type="button" onClick={() => sendAction({ type: "reload" })} title="Обновить"><RefreshCcw size={16} /></button>
          <button type="button" onClick={() => void closeAuth()} title="Закрыть"><X size={16} /></button>
        </div>
      </header>

      <div
        className="tbankrotRemoteScreen"
        tabIndex={0}
        role="application"
        aria-label="Экран авторизации TBankrot"
        onKeyDown={(event) => {
          if (event.ctrlKey || event.metaKey) {
            if (event.key.toLowerCase() === "a") {
              event.preventDefault();
              sendAction({ type: "key", key: event.metaKey ? "Meta+A" : "Control+A" });
            }
            return;
          }
          if (event.key.length === 1) {
            event.preventDefault();
            sendAction({ type: "text", value: event.key });
            return;
          }
          const supported = new Set([
            "Backspace", "Tab", "Enter", "Escape", "Delete",
            "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
            "Home", "End", "PageUp", "PageDown",
          ]);
          if (supported.has(event.key)) {
            event.preventDefault();
            sendAction({ type: "key", key: event.key });
          }
        }}
        onPaste={(event) => {
          const value = event.clipboardData.getData("text");
          if (!value) return;
          event.preventDefault();
          sendAction({ type: "text", value });
        }}
        onWheel={(event) => {
          event.preventDefault();
          sendAction({ type: "wheel", delta_y: event.deltaY });
        }}
      >
        {frameUrl
          ? <img
              src={frameUrl}
              alt="TBankrot"
              draggable={false}
              onClick={(event) => {
                const rect = event.currentTarget.getBoundingClientRect();
                const x = (event.clientX - rect.left) / rect.width * session.viewport.width;
                const y = (event.clientY - rect.top) / rect.height * session.viewport.height;
                sendAction({ type: "click", x, y });
                event.currentTarget.parentElement?.focus();
              }}
            />
          : <div className="tbankrotFrameLoading"><Loader2 className="spin" />Загрузка TBankrot…</div>}
      </div>
      <footer>
        <span>Кликните поле на экране и печатайте обычной клавиатурой. Пароль STERDEZ не сохраняет.</span>
        <button className="primaryButton" disabled={busy} onClick={() => void verify()}>
          {busy ? <Loader2 className="spin" size={16} /> : <ShieldCheck size={16} />}
          Проверить и продолжить
        </button>
      </footer>
    </section>}
  </section>;
}
