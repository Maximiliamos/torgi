import React from "react";

import { reportLotIssue } from "../../lib/lotIssueReportApi";

export function LotIssueReport({ lotId }: { lotId: number }) {
  const [open, setOpen] = React.useState(false);
  const [text, setText] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [message, setMessage] = React.useState("");
  const [error, setError] = React.useState("");

  React.useEffect(() => {
    setOpen(false);
    setText("");
    setMessage("");
    setError("");
  }, [lotId]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const content = text.trim();
    if (!content || busy) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await reportLotIssue(lotId, content);
      setText("");
      setOpen(false);
      setMessage("Спасибо. Сообщение об ошибке отправлено.");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Не удалось отправить сообщение");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mapIssueReport">
      <button
        type="button"
        className="mapIssueReportButton"
        onClick={() => {
          setOpen((value) => !value);
          setError("");
          setMessage("");
        }}
      >
        Сообщить об ошибке
      </button>
      {open && (
        <form className="mapIssueReportForm" onSubmit={submit}>
          <textarea
            value={text}
            onChange={(event) => setText(event.target.value)}
            maxLength={5000}
            placeholder="Опишите, что не так с этим лотом"
            aria-label="Описание ошибки лота"
            required
          />
          <div>
            <button type="submit" disabled={busy || !text.trim()}>
              {busy ? "Отправляем…" : "Отправить"}
            </button>
            <button type="button" onClick={() => setOpen(false)} disabled={busy}>
              Отмена
            </button>
          </div>
        </form>
      )}
      {message && <small className="mapIssueReportSuccess">{message}</small>}
      {error && <small className="mapIssueReportError">{error}</small>}
    </div>
  );
}
