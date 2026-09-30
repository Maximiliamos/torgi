import React from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ExternalLink,
  Loader2,
  LogIn,
  RefreshCcw,
  ShieldCheck,
  X,
} from "lucide-react";

import {
  ApiError,
  clickTBankrotAuth,
  closeTBankrotAuth,
  fetchNationwideLotSync,
  fetchTBankrotAuthStatus,
  fetchTBankrotScreenshot,
  keyTBankrotAuth,
  scrollTBankrotAuth,
  startTBankrotAuth,
  TBankrotAuthStatus,
  triggerTBankrotSync,
  typeTBankrotAuth,
  verifyTBankrotAuth,
} from "../../lib/api";

const POLL_STATUS_MS = 15_000;
const POLL_FRAME_MS = 900;
const REMOTE_KEYS = new Set([
  "Enter", "Tab", "Escape", "Backspace", "Delete",
  "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
  "Home", "End", "PageUp", "PageDown", " ",
]);

export function tbankrotAuthStatusLabel(value: TBankrotAuthStatus | null) {
  if (!value) return "Проверяем TBankrot";
  if (value.browser_active && !value.authenticated) return "Авторизация открыта";
  if (value.authenticated) return "Авторизация активна";
  if (value.requires_auth) return "Требуется авторизация";
  return "Источник временно недоступен";
}

function formatDate(value?: string | null) {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit",
  }).format(parsed);
}

export function TBankrotView({ refreshToken }: { refreshToken: number }) {
  const [status, setStatus] = React.useState<TBankrotAuthStatus | null>(null);
  const [frameUrl, setFrameUrl] = React.useState("");
  const [loading, setLoading] = React.useState(true);
  const [busy, setBusy] = React.useState("");
  const [error, setError] = React.useState("");
  const [syncId, setSyncId] = React.useState<string | null>(null);
  const [syncState, setSyncState] = React.useState("");
  const inputRef = React.useRef<HTMLTextAreaElement | null>(null);
  const lastBlobUrl = React.useRef("");

  const publishFrame = React.useCallback((blob: Blob) => {
    const next = URL.createObjectURL(blob);
    if (lastBlobUrl.current) URL.revokeObjectURL(lastBlobUrl.current);
    lastBlobUrl.current = next;
    setFrameUrl(next);
  }, []);

  const loadStatus = React.useCallback(async () => {
    try {
      const value = await fetchTBankrotAuthStatus();
      setStatus(value);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось проверить TBankrot");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadFrame = React.useCallback(async () => {
    try {
      publishFrame(await fetchTBankrotScreenshot());
    } catch (err) {
      if (!(err instanceof ApiError && err.status === 503)) {
        setError(err instanceof Error ? err.message : "Не удалось получить изображение TBankrot");
      }
    }
  }, [publishFrame]);

  React.useEffect(() => {
    void loadStatus();
    const timer = window.setInterval(() => void loadStatus(), POLL_STATUS_MS);
    return () => window.clearInterval(timer);
  }, [loadStatus, refreshToken]);

  React.useEffect(() => {
    if (!status?.browser_active) {
      setFrameUrl("");
      return;
    }
    void loadFrame();
    const timer = window.setInterval(() => void loadFrame(), POLL_FRAME_MS);
    return () => window.clearInterval(timer);
  }, [status?.browser_active, loadFrame]);

  React.useEffect(() => () => {
    if (lastBlobUrl.current) URL.revokeObjectURL(lastBlobUrl.current);
  }, []);

  React.useEffect(() => {
    if (!syncId) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const value = await fetchNationwideLotSync(syncId);
        if (cancelled) return;
        setSyncState(value.status);
        if (["success", "failed", "partial"].includes(value.status)) {
          setSyncId(null);
          void loadStatus();
        }
      } catch {
        if (!cancelled) setSyncState("unknown");
      }
    };
    void tick();
    const timer = window.setInterval(() => void tick(), 3_000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [syncId, loadStatus]);

  const start = async () => {
    setBusy("start"); setError("");
    try {
      const value = await startTBankrotAuth();
      setStatus(value);
      await loadFrame();
      window.setTimeout(() => inputRef.current?.focus(), 0);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось открыть TBankrot");
    } finally {
      setBusy("");
    }
  };

  const verify = async () => {
    setBusy("verify"); setError("");
    try {
      const value = await verifyTBankrotAuth();
      setStatus(value.auth);
      if (value.sync?.task_id) {
        setSyncId(value.sync.task_id);
        setSyncState(value.sync.status);
      } else if (value.sync?.status === "queue_unavailable") {
        setError("Авторизация сохранена, но очередь обновления сейчас недоступна.");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось проверить авторизацию");
    } finally {
      setBusy("");
    }
  };

  const sync = async () => {
    setBusy("sync"); setError("");
    try {
      const value = await triggerTBankrotSync();
      setSyncId(value.task_id);
      setSyncState(value.status);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось запустить TBankrot");
    } finally {
      setBusy("");
    }
  };

  const close = async () => {
    setBusy("close");
    try {
      await closeTBankrotAuth();
      setFrameUrl("");
      await loadStatus();
    } finally {
      setBusy("");
    }
  };

  const clickFrame = async (event: React.MouseEvent<HTMLImageElement>) => {
    if (!status?.browser_active) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const width = status.viewport?.width || 1280;
    const height = status.viewport?.height || 760;
    const x = (event.clientX - rect.left) * width / rect.width;
    const y = (event.clientY - rect.top) * height / rect.height;
    inputRef.current?.focus();
    await clickTBankrotAuth(x, y);
    void loadFrame();
  };

  const typeCharacter = async (text: string) => {
    if (!text || !status?.browser_active) return;
    try {
      await typeTBankrotAuth(text);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка ввода");
    }
  };

  const keyDown = async (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (!status?.browser_active) return;
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    if (REMOTE_KEYS.has(event.key)) {
      event.preventDefault();
      const key = event.key === " " ? "Space" : event.key;
      await keyTBankrotAuth(key);
      void loadFrame();
      return;
    }
    if (event.key.length === 1) {
      event.preventDefault();
      await typeCharacter(event.key);
    }
  };

  const paste = async (event: React.ClipboardEvent<HTMLTextAreaElement>) => {
    event.preventDefault();
    await typeCharacter(event.clipboardData.getData("text").slice(0, 512));
  };

  const authenticated = Boolean(status?.authenticated);
  const requiresAuth = Boolean(status?.requires_auth);
  const stateClass = authenticated
    ? "tbankrotState tbankrotState--ok"
    : requiresAuth
      ? "tbankrotState tbankrotState--warn"
      : "tbankrotState";

  return (
    <section className="tbankrotAuthPage">
      <div className="tbankrotHero">
        <div>
          <span className="eyebrow">Изолированный источник</span>
          <h2>TBankrot Auth Center</h2>
          <p>
            Авторизация выполняется в отдельной браузерной сессии. Логин, пароль и CAPTCHA
            не сохраняются STERDEZ — после входа сохраняются только cookies TBankrot.
          </p>
        </div>
        <div className={stateClass}>
          {authenticated ? <CheckCircle2 size={18} /> : <ShieldCheck size={18} />}
          <div>
            <strong>{tbankrotAuthStatusLabel(status)}</strong>
            <span>{status?.message || "Проверяем защищённую сессию…"}</span>
          </div>
        </div>
      </div>

      <div className="tbankrotStatusGrid">
        <article>
          <span>Сессия сохранена</span>
          <strong>{formatDate(status?.saved_at)}</strong>
        </article>
        <article>
          <span>Cookies</span>
          <strong>{status?.cookie_count ?? 0}</strong>
        </article>
        <article>
          <span>Лотов у источника</span>
          <strong>{status?.source_total ?? "—"}</strong>
        </article>
        <article>
          <span>Синхронизация</span>
          <strong>{syncId ? syncState || "queued" : "не запущена"}</strong>
        </article>
      </div>

      {error && <div className="tbankrotNotice tbankrotNotice--error"><AlertTriangle size={17} />{error}</div>}
      {syncId && <div className="tbankrotNotice"><Loader2 className="spin" size={17} />TBankrot обновляется отдельно от остальных источников · {syncState}</div>}

      {!status?.browser_active && (
        <section className="tbankrotActionCard">
          <div>
            <h3>{authenticated ? "Источник готов к работе" : "Нужен вход в TBankrot"}</h3>
            <p>
              {authenticated
                ? "Сохранённая сессия подтверждена. Можно запустить отдельное полное обновление TBankrot."
                : "Откройте TBankrot ниже, войдите в свою учётную запись и пройдите CAPTCHA, если сайт её покажет."}
            </p>
          </div>
          <div className="tbankrotActions">
            <button className="primaryButton" disabled={Boolean(busy)} onClick={() => void start()}>
              {busy === "start" ? <Loader2 className="spin" size={16} /> : <LogIn size={16} />}
              {authenticated ? "Открыть TBankrot" : "Войти в TBankrot"}
            </button>
            {authenticated && (
              <button className="secondaryButton" disabled={Boolean(busy) || Boolean(syncId)} onClick={() => void sync()}>
                <RefreshCcw size={15} />Обновить TBankrot сейчас
              </button>
            )}
          </div>
        </section>
      )}

      {status?.browser_active && (
        <section className="tbankrotBrowserCard">
          <header>
            <div>
              <strong>{status.page_title || "TBankrot"}</strong>
              <span>{status.page_url || "https://tbankrot.ru/"}</span>
            </div>
            <div>
              <a href="https://tbankrot.ru/" target="_blank" rel="noreferrer" title="Открыть сайт отдельно">
                <ExternalLink size={15} />
              </a>
              <button type="button" onClick={() => void close()} title="Закрыть сессию">
                <X size={16} />
              </button>
            </div>
          </header>
          <div
            className="tbankrotBrowserViewport"
            onWheel={(event) => {
              event.preventDefault();
              void scrollTBankrotAuth(event.deltaY).then(loadFrame).catch(() => undefined);
            }}
          >
            {frameUrl
              ? <img
                  src={frameUrl}
                  alt="Интерактивная сессия TBankrot"
                  draggable={false}
                  onClick={(event) => void clickFrame(event)}
                />
              : <div className="tbankrotBrowserLoading"><Loader2 className="spin" />Загрузка страницы TBankrot…</div>}
            <textarea
              ref={inputRef}
              className="tbankrotKeyboardCapture"
              aria-label="Ввод в удалённую сессию TBankrot"
              value=""
              onChange={() => undefined}
              onKeyDown={(event) => void keyDown(event)}
              onPaste={(event) => void paste(event)}
            />
          </div>
          <footer>
            <span>Кликните по полю на странице и печатайте как обычно. CAPTCHA проходит пользователь вручную.</span>
            <button className="primaryButton" disabled={busy === "verify"} onClick={() => void verify()}>
              {busy === "verify" ? <Loader2 className="spin" size={16} /> : <ShieldCheck size={16} />}
              Я вошёл — проверить и продолжить
            </button>
          </footer>
        </section>
      )}

      {loading && !status && <div className="tbankrotNotice"><Loader2 className="spin" size={17} />Проверяем TBankrot…</div>}
    </section>
  );
}
