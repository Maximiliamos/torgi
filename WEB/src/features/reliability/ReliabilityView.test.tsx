import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ReliabilityView } from "./ReliabilityView";
import { fetchReliabilitySources, fetchReliabilitySummary } from "./api";
import type { ReliabilitySource, ReliabilitySummary } from "./api";
import { fetchCapabilities, fetchOperationsProgress, fetchQuality } from "../../lib/api";

vi.mock("./api", () => ({ fetchReliabilitySources: vi.fn(), fetchReliabilitySummary: vi.fn() }));
vi.mock("../../lib/api", () => ({
  fetchCapabilities: vi.fn(), fetchOperationsProgress: vi.fn(), fetchQuality: vi.fn(), probeSourceHealth: vi.fn(),
}));

function summary(population = 12): ReliabilitySummary {
  return {
    schema_version: 1, generated_at: "2026-11-19T04:00:00Z",
    system: { status: "degraded", reasons: ["Нужна проверка"], deployment_sha: "abcd1234" },
    sources: { configured_total: 1, healthy: 0, degraded: 1, failed: 0, paused_total: 0, not_checked: 0 },
    geo: { total: population, geocoded: 6, percent: 50, actionable_remaining: 3 },
    map: { ok: false, delivery_status: "failed", dataset_status: "ready", public_manifest: { checked: true, ok: false } },
    integrity: { current_dataset_count: 1, critical_count: 0, warning_count: 0 },
    host: { read_only: true, disk: { free_gb: 40, status: "ok" }, queue: { status: "disabled" } },
    data_quality: { population, missing_address: 1, missing_cadastre: 0, missing_price: 0,
      unknown_status: 0, missing_region: 0, missing_url: 0, non_positive_price: 0, percentages: { missing_address: 8.3 } },
  } as unknown as ReliabilitySummary;
}

function source(name: string, status: string): ReliabilitySource {
  return { canonical_id: name + ".ru", display_name_ru: name, aliases: [], configured: true,
    overall_status: status, freshness_status: "fresh", coverage_status: "fresh",
    current_error: null, last_historical_error: null, last_success_at: "2026-11-19T04:00:00Z",
  } as unknown as ReliabilitySource;
}

beforeEach(() => {
  vi.mocked(fetchReliabilitySummary).mockResolvedValue(summary());
  vi.mocked(fetchReliabilitySources).mockResolvedValue({ items: [source("Первый", "degraded")] } as Awaited<ReturnType<typeof fetchReliabilitySources>>);
  vi.mocked(fetchOperationsProgress).mockResolvedValue({ journal: [] } as unknown as Awaited<ReturnType<typeof fetchOperationsProgress>>);
  vi.mocked(fetchCapabilities).mockResolvedValue({ background_jobs: false } as Awaited<ReturnType<typeof fetchCapabilities>>);
  vi.mocked(fetchQuality).mockResolvedValue({ total_lots: 12 } as Awaited<ReturnType<typeof fetchQuality>>);
});

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.clearAllMocks(); });

describe("Reliability acceptance gaps", () => {
  it("shows the dashboard while supplementary journal, capabilities and history are pending", async () => {
    vi.mocked(fetchOperationsProgress).mockImplementationOnce(() => new Promise(() => {}));
    vi.mocked(fetchCapabilities).mockImplementationOnce(() => new Promise(() => {}));
    vi.mocked(fetchQuality).mockImplementationOnce(() => new Promise(() => {}));
    render(<ReliabilityView refreshToken={0} role="reader" />);
    expect(await screen.findByRole("heading", { name: /Источники данных/ })).toBeVisible();
    expect(screen.getByRole("button", { name: "Обновить" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Экспорт отчёта" })).toBeEnabled();
    expect(screen.queryByText("Загружаем результаты проверки…")).not.toBeInTheDocument();
  });

  it.each([
    { healthy: 0, degraded: 0, failed: 0, paused_total: 8, not_checked: 0, tone: "rel-textNeutral" },
    { healthy: 0, degraded: 0, failed: 0, paused_total: 0, not_checked: 8, tone: "rel-textNeutral" },
    { healthy: 3, degraded: 0, failed: 0, paused_total: 5, not_checked: 0, tone: "rel-textWarn" },
    { healthy: 8, degraded: 0, failed: 0, paused_total: 0, not_checked: 0, tone: "rel-textOk" },
  ])("does not mark paused or unchecked sources green: $healthy healthy, $paused_total paused", async state => {
    const data = summary();
    data.sources = { ...data.sources, ...state, configured_total: 8 };
    vi.mocked(fetchReliabilitySummary).mockResolvedValue(data);
    render(<ReliabilityView refreshToken={0} role="reader" />);
    const value = await screen.findByText(`${state.healthy} из 8 без замечаний`);
    expect(value).toHaveClass(state.tone);
    expect(within(value).getByText(`На паузе: ${state.paused_total} · нет проверки: ${state.not_checked}`)).toBeVisible();
  });

  it("does not describe a failed manifest as verified and keeps write actions hidden", async () => {
    render(<ReliabilityView refreshToken={0} role="reader" />);
    expect(await screen.findByText("Ошибка доставки")).toBeVisible();
    expect(screen.getByText("Проверка выявила ошибку")).toBeVisible();
    expect(screen.queryByText("Сверены версия и размеры")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Проверить источник/ })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "GEO · координаты" })).toBeVisible();
  });

  it("keeps successful values and marks the failed refresh stale", async () => {
    render(<ReliabilityView refreshToken={0} role="reader" />);
    await within(await screen.findByRole("table")).findByText("Первый");
    vi.mocked(fetchReliabilitySummary).mockRejectedValueOnce(new Error("temporary"));
    vi.mocked(fetchReliabilitySources).mockRejectedValueOnce(new Error("temporary"));
    fireEvent.click(screen.getByRole("button", { name: "Обновить" }));
    expect(await screen.findByText(/Не удалось обновить: Состояние, Источники/)).toBeVisible();
    expect(within(screen.getByRole("table")).getByText("Первый")).toBeVisible();
    expect(screen.getByText("Ошибка доставки")).toBeVisible();
    expect(screen.getByText(/Устарели: Состояние, Источники/)).toBeVisible();
  });

  it("sorts failed sources first and localizes an unknown enum", async () => {
    vi.mocked(fetchReliabilitySources).mockResolvedValue({ items: [
      source("Здоровый", "healthy"), source("Сломанный", "failed"), source("Новый", "future_enum"),
    ] } as Awaited<ReturnType<typeof fetchReliabilitySources>>);
    render(<ReliabilityView refreshToken={0} role="reader" />);
    await within(await screen.findByRole("table")).findByText("Сломанный");
    const rows = within(screen.getByRole("table", { name: "Состояние источников" })).getAllByRole("row");
    expect(rows[1]).toHaveTextContent("Сломанный");
    expect(screen.getByText("Неизвестно")).toBeVisible();
    expect(screen.queryByText("future_enum")).not.toBeInTheDocument();
  });

  it("keeps raw transport errors in expanded technical details", async () => {
    const row = { ...source("Проверяемый", "failed"), current_error: "HTTPConnectionPool: Read timed out secret-free-detail" };
    vi.mocked(fetchReliabilitySources).mockResolvedValue({ items: [row] } as Awaited<ReturnType<typeof fetchReliabilitySources>>);
    render(<ReliabilityView refreshToken={0} role="reader" />);
    const table = await screen.findByRole("table");
    expect(within(table).getByText("Источник недоступен или не ответил вовремя")).toBeVisible();
    expect(screen.queryByText(/secret-free-detail/)).not.toBeInTheDocument();
    fireEvent.click(within(table).getAllByRole("row")[1]);
    expect(await screen.findByText(/Техническая ошибка: HTTPConnectionPool/)).toBeVisible();
  });

  it("opens a source problem even when the table filter hides that source", async () => {
    vi.mocked(fetchReliabilitySources).mockResolvedValue({ items: [source("Проверяемый", "failed")] } as Awaited<ReturnType<typeof fetchReliabilitySources>>);
    render(<ReliabilityView refreshToken={0} role="reader" />);
    await screen.findByRole("table");
    fireEvent.click(screen.getByRole("button", { name: /Стабильные/ }));
    expect(screen.getByText("Источники по заданным условиям не найдены.")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /Проверяемый/ }));
    const table = screen.getByRole("table");
    await waitFor(() => expect(within(table).getAllByRole("row")[1]).toHaveAttribute("aria-expanded", "true"));
    expect(screen.getByText("Исторические имена")).toBeVisible();
  });

  it("downloads a JSON report without rendering raw JSON on the page", async () => {
    const create = vi.fn((blob: Blob) => "blob:diagnostics-" + blob.size);
    Object.defineProperty(URL, "createObjectURL", { value: create, configurable: true });
    Object.defineProperty(URL, "revokeObjectURL", { value: vi.fn(), configurable: true });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    render(<ReliabilityView refreshToken={0} role="reader" />);
    const button = await screen.findByRole("button", { name: "Экспорт отчёта" });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
    expect(click).toHaveBeenCalledOnce();
    const blob = create.mock.calls[0][0] as Blob;
    const text = await new Promise<string>(resolve => {
      const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.readAsText(blob);
    });
    expect(JSON.parse(text).schema_version).toBe(1);
    expect(document.querySelector("pre")).toBeNull();
  });

  it("discards a slow earlier refresh after a newer refresh succeeds", async () => {
    let resolveOld!: (value: ReliabilitySummary) => void;
    vi.mocked(fetchReliabilitySummary).mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; }));
    const view = render(<ReliabilityView refreshToken={0} role="reader" />);
    await waitFor(() => expect(fetchReliabilitySummary).toHaveBeenCalledOnce());
    vi.mocked(fetchReliabilitySummary).mockResolvedValue(summary(99));
    view.rerender(<ReliabilityView refreshToken={1} role="reader" />);
    await waitFor(() => expect(screen.getAllByText("99")[0]).toBeVisible());
    await act(async () => { resolveOld(summary(22)); });
    expect(screen.getAllByText("99")[0]).toBeVisible();
    expect(screen.queryByText("22")).not.toBeInTheDocument();
  });
});
