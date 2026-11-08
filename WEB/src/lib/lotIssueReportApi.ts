import { requestJson } from "./api";

export const reportLotIssue = (lotId: number, content: string) =>
  requestJson<{ id: number; lot_id: number; content: string; created_at: string }>(
    `/api/lots/${lotId}/report-error`,
    undefined,
    { method: "POST", body: JSON.stringify({ content }) },
  );
