# Инвентаризация GitHub Actions и scripts — BankrotAI

**Дата:** 20.11.2026. **Основа:** `main` `bbebd60556bb3c242e7eec0b990cc552d0f98e61`. **Режим:** read-only по продукту и данным; на этой ветке изменены только аудиторские файлы. **Удалено: 0. Отключено: 0.**

## 1. Что проверено и как читать отчёт

Просмотрены содержимое **57 файлов** `.github/workflows/` и **114 файлов** `scripts/` на точном исходном SHA: заголовки и триггеры, текстовые вызовы скриптов (включая `./scripts/` и PowerShell `.\\scripts\\`), 19 определений `workflow_run`, маркеры записи/удаления и строки с существующими guards. Дополнительно просмотрены последние 600 GitHub Actions runs за **18–20 ноября** (примерно 48 часов). Это временное окно **не** доказывает неиспользование более редкого процесса.

Сохранены машиночитаемые приложения:
- [Классификация по всем 171 файлам](./AUTOMATION_CLASSIFICATION_2026-11-20.csv) — категория, риски, вызовы, пересечения, последние наблюдения, SHA каждого файла.
- [Workflow inventory](./WORKFLOW_INVENTORY_2026-11-20.jsonl) — триггеры, cron, текстовые сигналы и guards.
- [Script inventory](./SCRIPT_INVENTORY_2026-11-20.jsonl) — расширения, вызовы, маркеры изменения и guards.
- [Workflow call graph](./WORKFLOW_CALL_GRAPH_2026-11-20.jsonl) — Windows/POSIX пути к скриптам, зависимости по именам `workflow_run`.

**Категории не взаимоисключающие по смыслу.** Основная роль файла отражена в столбце «Класс», а признаки пересечения/опасной операции — отдельно. `P0-review/P1-review` — приоритет **ручного ревью**, а не доказанный дефект; текстовый матч `apply`, `DELETE` и т.п. может находиться в комментариях, тестах и read-only проверках. «Не установлен» означает только отсутствие найденного *прямого вызова внутри просмотренного набора*, не разрешение на удаление.

## 2. Фактический объём и связи

| Метрика | Результат |
|---|---:|
| Workflow / scripts | **57 / 114** |
| Workflow с автоматическим триггером | **43** (включая разовые с активным триггером) |
| Workflow только с ручным триггером | **14** |
| Workflow с `workflow_dispatch` | 49 |
| Workflow с `push`, `pull_request`, `schedule` | 22 / 7 / 9 |
| Workflow с `workflow_run` | 19 |
| Обнаруженные зависимости `workflow_run` по имени | 23, все указывают на существующие workflow |
| Скрипты с прямым ссылочным вызовом внутри workflows/scripts | **48** |
| Скрипты без такого прямого вызова | **66**, использование извне не проверено |
| В том числе явно датированные / генераторы / миграция | 3 скрипта, из них датированный аудит ещё вызывается другим скриптом |
| Workflow с явным one-time/legacy обозначением | 3 (см. P0/P1 ниже) |
| Workflow, наблюдавшиеся в последних 600 Actions runs | 30; остальные 27 не наблюдались в коротком окне |
| Группы потенциального функционального перекрытия | 8 групп, **не** доказанные дубли |

Из 48 скриптов с прямыми внутренними вызовами **32** упоминаются workflow, остальные **16** — только другими скриптами (с учётом точных литералов путей). `WEB/scripts/check-build-audit*.mjs` выполняются из `working-directory: WEB`; их нельзя объявлять отсутствующими из-за совпадения подстроки `scripts/` с корневым каталогом.

Не проверены внешние Windows Task Scheduler, локальные `.bat`-ярлыки вне репозитория, пользовательские ручные команды, приватные серверные cron, внешние интеграции и полная 90-дневная история Actions. **Ни один файл не получил статус `safe_to_delete`.**

## 3. Обнаруженные приоритетные риски

| Приоритет | Файл | Подтверждённое поведение | Решение для следующего PR |
|---|---|---|---|
| **P0 audit** | `branch-hygiene.yml` | `push` + недельный `schedule`; код вызывает GitHub API `DELETE` для merged/contained веток | Ревью protected/active refs, исключения PR и ручной стоп-кран; не ломать housekeeping вслепую |
| **P0 audit** | `pr-one-time-s3-orphan-cleanup.yml` | `pull_request` по изменению собственного workflow; Python вызывает S3 `delete-objects` по заданным orphan-префиксам | После подтверждения закончившейся кампании убрать автоматический destructive trigger / перевести в явный подтверждаемый run, НЕ запускать для проверки |
| **P1 audit** | `regru-s3-diagnostics.yml` | PR-режим без секретов, но ручной основной режим может делать `put-bucket-cors`, писать и удалять probe | Разделить read-only аудит и изменяющий конфигурацию инструмент; сохранять ACL и результат проверки |
| **P1 audit** | `cdtrf-one-time-import-geo-map.yml` | Называется разовым, но имеет `workflow_run` от P11 | Проверить выполненность кампании и актуальный gate, затем убрать авто-триггер только через отдельный PR |
| **P1 audit** | `etp-verified-canary-geo-map.yml` | Автоматическая зависимость от S5; сценарий может писать данные, делать GEO и публиковать карту | Подтвердить точные SHA, backup, ограниченную выборку, запрет самопроизвольного массового импорта |
| **P1 audit** | `phase3-lite-full-reconcile.yml` | `push` формально присутствует, **но write jobs имеют `if: github.event_name == 'workflow_dispatch'`** | Сохранить защиту. Упростить диспетчеризацию отдельно, не объявлять автозаписью на каждый push |
| **P1 audit** | `manual-seven-source-refresh.yml` | `push` присутствует, но job допускает dispatch или специальную метку commit `[run-seven-source-now]` | Проводить ручную приёмку commit-message триггера и предотвращения повторных очередей |
| **P1 audit** | `etp-all-new-sources-import-geo-map.yml` | Ручной большой import+GEO+map; требует accepted_sha и P1 | Сохранить read-only preview, точную allowlist, проверенные backup/P1/P11 до каждого разрешения |
| **P1 audit** | `regru-s3-reference-gc.yml` и `map-storage-compaction.yml` | Ручные изменяющие storage операции с подтверждениями | Не смешивать разные политики удаления объектов; отдельные approval/rollback gates |
| **P1 audit** | `regru-cloudflare-tunnel.yml`, `regru-direct-dns-cutover.yml` | Legacy/emergency и сценарий изменения DNS | Сначала актуальная схема подключения и DR, затем решение о переводе в архив |

**Важно:** `branch-hygiene` и `pr-one-time-s3-orphan-cleanup` действительно содержат удаляющие операции и триггеры. Остальные перечисленные пункты требуют ревью границ, но само наличие `--apply` в комментарии либо в тестовом примере не означает, что workflow автоматически меняет production.

## 4. Кандидаты на объединение — не на удаление

| Группа | Файлы | Безопасное направление |
|---|---|---|
| Диагностика Home | `home-origin-diagnostics`, `origin-availability-diagnostics`, `home-performance-diagnostics`, `home-runner-network-diagnostics` | Единая точка входа и формат отчёта, разная глубина проверки |
| ЭТП: базовый аудит | `etp-22-source-readonly-inventory`, `etp-parser-stage-checklist`, `etp-manual-qa-all-sources` | Общая матрица S0–S7/ошибок без изменения source-specific тестов |
| ЦДТ | `cdtrf-source-diagnostics`, `etp-cdtrf-full-catalogue-readonly`, `etp-cdtrf-pagination-readonly` | Один операторский маршрут: 5 страниц → полный каталог → детали |
| Восстановление source | `source-circuit-postdeploy-recovery`, `torgi-russia-postdeploy-recovery`, `torgi-russia-home-readonly-recovery` | Проверить условия восстановления и объединить только общие guard-функции |
| S3 | `regru-s3-diagnostics`, `regru-s3-reference-gc`, `pr-one-time-s3-orphan-cleanup` | Общая *диагностика*, но разные и раздельно подтверждаемые операции удаления |
| Захват ЭТП | `etp_capture_v6.py`, `RUN_ETP_CAPTURE_V6_WINDOWS.bat`, `capture-*-no-vpn.ps1` | Один движок + профили запуска. `.bat/.ps1` часто лишь wrappers, не доказанные дубли |
| GEO repair | `geo_bidexpert_detail_repair.py`, `geo_property_address_repair.py`, `geo_stale_cadastral_recovery.py`, `geo_torgi_gov_detail_repair.py` | Общий набор safety guards (preview/CAS/rollback), без подмены источниковых доказательств |
| Source repair | `apply-gov-verified-batches.ps1`, `apply-verified-source-repair.ps1`, `repair_closed_projection_invariants.py` и wrappers | Общая процедура approval/evidence; операции остаются отдельными |

Сходные имена, общий исполняемый модуль и общее назначение **не** устанавливают побайтовое или функциональное дублирование. Для удаления потребуется сравнить входы, эффекты, permissions, guards и реальную историю вызовов.

## 5. Исторические и разовые кандидаты

- `cdtrf-one-time-import-geo-map.yml` — разовая кампания с **сохранившимся workflow_run**, сначала проверить завершённость и зависимости.
- `pr-one-time-s3-orphan-cleanup.yml` — разовая destructive-кампания с PR-триггером, приоритет P0.
- `regru-cloudflare-tunnel.yml` — прямо обозначен `LEGACY EMERGENCY ONLY`; может оставаться необходимым для DR.
- `audit_20261108_nationwide_readonly.py` — датированный отчёт, **используется** `run-readonly-postrun-audit-home.ps1`; не удалять.
- `generate_app_icon.py` — похоже на разовую генерацию, но сначала проверить воспроизводимость desktop assets.
- `migrate_sqlite_to_postgres.py` — миграционная утилита, нужно сохранить recovery-процедуру и совместимость до архивирования.

`etp_capture_v6.py`, `RUN_ETP_CAPTURE_V6_WINDOWS.bat` и `etp_aukcioncenter_audit.py` **не отмечены как подтверждённо разовые**: версия v6 и подстрока «once» внутри слова Aukcioncenter не являются признаком одноразового кода.

## 6. Порядок безопасного сокращения

1. **P0 — ревью автоматических destructive workflow.** Проверить `branch-hygiene` и `pr-one-time-s3-orphan-cleanup`: их фактические разрешения, триггеры, последние действия, protected refs/наборы S3. Предложение disable/cutover — отдельно, после подтверждения владельца.
2. **P1 — довести карту вызовов.** Проверить внешние Windows scheduler/shortcuts, Home runner tasks, private cron, использованные Docker commands, историю GitHub Actions минимум за 90 дней, ссылки в `README/docs` и активные PR. У 66 скриптов нет прямой обнаруженной ссылки, это очередь на дополнительную проверку.
3. **P1 — только экспериментальное объединение диагностики.** Выбрать Home-readonly или ЭТП-readonly группу, сравнить выходные JSON/ошибки до/после; сохранить старую команду как совместимый wrapper на время перехода.
4. **P1 — декомпозировать изменяющие сценарии, не склеивая их.** Общий безопасный preflight (exact-SHA, backup restore, preview, CAS, lease) и разные write-операции.
5. **P2 — исторические one-time workflow.** После доказательства окончания кампании снять автоматические триггеры либо перенести в архив вне `.github/workflows/` отдельным PR; сохранить исходный код и run artifacts.
6. **P2 — удаление только после проверок.** Обязательны `git grep/rg`, Actions history, CLI/PowerShell aliases, Task Scheduler, owner review, CI и rollback plan. У каждого файла есть владелец, последнее использование, заменяющая команда, reversible change и доказательство отсутствия зависимости.
7. **После каждого шага:** 10/10 CI, dry-run, проверка read-only и exact-SHA production; никакого одновременного массового удаления и переделки реальных импортеров ЭТП.

**Статус аудита:** классификация оформлена; список на прямое удаление **пустой**. Следующий шаг — снять блокеры по внешним зависимостям и отдельно устранить автоматические destructive-триггеры.

---

## 7. Полный перечень 57 workflows

`P0/P1` ниже означает ручную проверку риска. Пересечение — **кандидат на унификацию**, не дубль.

| Файл | Класс | Триггеры | Ревью | Пересечение |
|---|---|---|---|---|
| `branch-disposition-audit.yml` | Авто | workflow_dispatch / push | — | — |
| `branch-hygiene.yml` | Авто | workflow_dispatch / schedule / push | **P0** | — |
| `cdtrf-one-time-import-geo-map.yml` | Ист./разовый? | workflow_dispatch / workflow_run | P1 | — |
| `cdtrf-source-diagnostics.yml` | Авто | workflow_dispatch / push | — | diag-cdtrf-overlap |
| `ci.yml` | Авто | push / pull_request | — | — |
| `cloudflare-edge-deploy.yml` | Авто | workflow_dispatch / push / pull_request | P1 | — |
| `etp-22-source-readonly-inventory.yml` | Авто | workflow_dispatch / workflow_run / schedule | — | diag-etp-overlap |
| `etp-all-new-sources-import-geo-map.yml` | Ручной | workflow_dispatch | P1 | — |
| `etp-cdtrf-full-catalogue-readonly.yml` | Авто | push / workflow_dispatch | — | diag-cdtrf-overlap |
| `etp-cdtrf-pagination-readonly.yml` | Авто | workflow_dispatch / workflow_run | — | diag-cdtrf-overlap |
| `etp-home-readonly-smoke.yml` | Авто | workflow_run | — | — |
| `etp-manual-qa-all-sources.yml` | Авто | workflow_dispatch / workflow_run | — | diag-etp-overlap |
| `etp-manual-verified-import.yml` | Ручной | workflow_dispatch | P1 | — |
| `etp-mets-pagination-readonly.yml` | Авто | workflow_dispatch / workflow_run | — | — |
| `etp-parser-stage-checklist.yml` | Авто | workflow_dispatch / workflow_run | — | diag-etp-overlap |
| `etp-ruson-contract-readonly.yml` | Авто | push / workflow_dispatch | — | — |
| `etp-s5-canary-readiness.yml` | Авто | workflow_run | — | — |
| `etp-verified-canary-geo-map.yml` | Авто | workflow_run | P1 | — |
| `geo-managed-stale-recovery.yml` | Ручной | workflow_dispatch | P1 | — |
| `geo-p0-readonly.yml` | Авто | workflow_dispatch / push | — | — |
| `geocode-backfill.yml` | Ручной | workflow_dispatch | P1 | — |
| `home-origin-diagnostics.yml` | Ручной | workflow_dispatch | — | diag-home |
| `home-performance-diagnostics.yml` | Ручной | workflow_dispatch | — | diag-home |
| `home-reverse-relay.yml` | Авто | workflow_dispatch / push | P1 | — |
| `home-runner-network-diagnostics.yml` | Ручной | workflow_dispatch | — | diag-home |
| `home-secondary-deploy.yml` | Авто | workflow_dispatch / push | P1 | — |
| `lot-issue-reports-export.yml` | Авто | workflow_dispatch / pull_request / push / schedule | — | — |
| `manual-seven-source-refresh.yml` | Авто | workflow_dispatch / push | P1 | — |
| `map-storage-compaction.yml` | Ручной | workflow_dispatch | P1 | — |
| `origin-availability-diagnostics.yml` | Ручной | workflow_dispatch | — | diag-home |
| `p1-data-quality.yml` | Авто | workflow_dispatch / push / schedule / workflow_run | — | — |
| `p11-production-acceptance.yml` | Авто | push / workflow_dispatch | — | — |
| `p13-historical-region-review.yml` | Авто | workflow_dispatch / workflow_run | — | — |
| `p16-read-only-baseline.yml` | Авто | workflow_dispatch / workflow_run / push | — | — |
| `p2-production-maintenance.yml` | Авто | workflow_dispatch / schedule / workflow_run | P1 | — |
| `p7-geo-fast-drain.yml` | Авто | workflow_dispatch / push | P1 | — |
| `p9-geo-quality-canary.yml` | Авто | push / workflow_dispatch | P1 | — |
| `phase3-lite-backup.yml` | Авто | workflow_dispatch / schedule / push | P1 | — |
| `phase3-lite-full-reconcile.yml` | Авто | workflow_dispatch / push | P1 | — |
| `phase3-lite-health.yml` | Авто | workflow_dispatch / schedule / workflow_run | — | — |
| `postfix-seven-source-verification.yml` | Авто | push / workflow_dispatch | — | — |
| `pr-home-map-retention-diagnostics.yml` | Авто | pull_request | — | — |
| `pr-one-time-s3-orphan-cleanup.yml` | Ист./разовый? | pull_request | **P0** | s3-different-destruction-policy |
| `production-functional.yml` | Авто | workflow_dispatch / workflow_run / schedule | — | — |
| `public-web-smoke.yml` | Авто | workflow_dispatch / workflow_run / schedule | — | — |
| `recover-superseded-release-runs.yml` | Авто | workflow_dispatch / push | — | — |
| `regru-cloudflare-tunnel.yml` | Ист./разовый? | workflow_dispatch | P1 | — |
| `regru-deploy.yml` | Авто | workflow_dispatch / push | P1 | — |
| `regru-direct-dns-cutover.yml` | Ручной | workflow_dispatch | P1 | — |
| `regru-s3-diagnostics.yml` | Авто | pull_request / workflow_dispatch | — | s3-different-destruction-policy |
| `regru-s3-reference-gc.yml` | Ручной | workflow_dispatch | P1 | s3-different-destruction-policy |
| `release.yml` | Авто | workflow_run | — | — |
| `reported-source-refresh.yml` | Ручной | workflow_dispatch | P1 | — |
| `source-circuit-postdeploy-recovery.yml` | Авто | workflow_run / workflow_dispatch | P1 | recovery-source-overlap |
| `sterdez-domain-preflight.yml` | Ручной | workflow_dispatch | — | — |
| `torgi-russia-home-readonly-recovery.yml` | Авто | pull_request / workflow_dispatch | — | recovery-source-overlap |
| `torgi-russia-postdeploy-recovery.yml` | Авто | workflow_run | P1 | recovery-source-overlap |

## 8. Полный перечень 114 scripts

«Есть вызов» означает обнаруженный прямой вызов внутри обследованных файлов. «Не установлен» — отсутствие такого вызова, а не устаревание.

| Файл | Класс | Прямых вызывающих | Ревью | Пересечение |
|---|---|---:|---|---|
| `RUN_ETP_CAPTURE_V6_WINDOWS.bat` | Не установлен | — | — | etp-capture-wrappers |
| `accept_three_sources_nonreconciling.py` | Не установлен | — | — | — |
| `apply-duplicate-link-repair.ps1` | Не установлен | — | P1 | — |
| `apply-gov-verified-batches.ps1` | Не установлен | — | P1 | source-verified-repair-wrappers |
| `apply-reported-lot-resolution.ps1` | Не установлен | — | P1 | source-verified-repair-wrappers |
| `apply-verified-nspd.ps1` | Не установлен | — | P1 | — |
| `apply-verified-source-repair.ps1` | Есть вызов | 1 | P1 | source-verified-repair-wrappers |
| `apply_duplicate_link_repair.py` | Есть вызов | 1 | P1 | — |
| `apply_reg_s3_orphans.py` | Не установлен | — | P1 | s3-different-destruction-policy |
| `apply_verified_nspd.py` | Есть вызов | 1 | P1 | — |
| `audit_20261108_nationwide_readonly.py` | Есть вызов | 1 | — | — |
| `audit_centerr_contract.py` | Не установлен | — | — | — |
| `audit_fabrikant_readonly.py` | Не установлен | — | — | — |
| `audit_sberbank_ast_bankruptcy_readonly.py` | Не установлен | — | — | — |
| `audit_source_connectivity.py` | Не установлен | — | — | — |
| `audit_zakazrf_readonly.py` | Не установлен | — | — | — |
| `backup-home-postgres.ps1` | Есть вызов | 1 | P1 | — |
| `bidexpert_detail_probe.py` | Есть вызов | 1 | — | — |
| `branch_disposition_audit.py` | Есть вызов | 1 | — | — |
| `capture-five-blocked-etp-no-vpn.ps1` | Не установлен | — | — | etp-capture-wrappers |
| `capture-profit-ausib-no-vpn.ps1` | Не установлен | — | — | etp-capture-wrappers |
| `capture-reported-auth-details.mjs` | Не установлен | — | — | — |
| `capture-seven-etp-no-vpn.ps1` | Не установлен | — | — | etp-capture-wrappers |
| `capture-tbankrot-session-no-vpn.ps1` | Не установлен | — | — | — |
| `capture-tbankrot-session.mjs` | Не установлен | — | — | — |
| `check-repository-consistency.py` | Есть вызов | 2 | — | — |
| `check_action_pins.py` | Есть вызов | 1 | — | — |
| `check_module_sizes.py` | Есть вызов | 1 | — | — |
| `compact-map-storage.ps1` | Есть вызов | 1 | P1 | — |
| `desktop-package-smoke.ps1` | Не установлен | — | — | — |
| `diagnose-standard-source-circuits-no-vpn.ps1` | Не установлен | — | P1 | — |
| `diagnose_mets_public_origins.py` | Есть вызов | 1 | — | — |
| `docker-smoke-lib.sh` | Есть вызов | 2 | — | — |
| `docker-smoke-operator.sh` | Есть вызов | 1 | P1 | — |
| `docker-smoke-readonly.sh` | Есть вызов | 1 | P1 | — |
| `docker-smoke.sh` | Есть вызов | — | — | — |
| `ensure-docker-for-backup.ps1` | Есть вызов | 2 | — | — |
| `etp-canary-readiness.ps1` | Есть вызов | 1 | P1 | — |
| `etp_aukcioncenter_audit.py` | Не установлен | — | — | — |
| `etp_capture_v6.py` | Есть вызов | 4 | — | etp-capture-wrappers |
| `etpu_filter_probe.py` | Не установлен | — | — | — |
| `etpu_readonly_audit.py` | Не установлен | — | — | — |
| `export_gov_ingestion_evidence.py` | Не установлен | — | — | — |
| `export_verified_backup_offhost.py` | Не установлен | — | P1 | — |
| `generate_app_icon.py` | Ист./разовый? | — | — | — |
| `geo_bidexpert_detail_repair.py` | Не установлен | — | P1 | geo-repair-shared-guards |
| `geo_no_match_live_probe.py` | Есть вызов | 1 | — | — |
| `geo_p0_provider_probe.py` | Не установлен | — | — | — |
| `geo_p0_readonly.py` | Есть вызов | — | — | — |
| `geo_property_address_repair.py` | Не установлен | — | P1 | geo-repair-shared-guards |
| `geo_stale_cadastral_recovery.py` | Не установлен | — | P1 | geo-repair-shared-guards |
| `geo_torgi_gov_detail_repair.py` | Не установлен | — | P1 | geo-repair-shared-guards |
| `home-origin-diagnostics.ps1` | Есть вызов | 2 | — | diag-home |
| `home-production-health.ps1` | Не установлен | — | — | diag-home |
| `home-runner-network-diagnostics.ps1` | Есть вызов | 1 | — | diag-home |
| `launch-seven-source-gated.ps1` | Есть вызов | 1 | — | — |
| `load-test-readonly.py` | Есть вызов | 1 | — | — |
| `mets-readonly-acceptance.py` | Не установлен | — | — | — |
| `migrate-backups-to-d.ps1` | Не установлен | — | P1 | — |
| `migrate_sqlite_to_postgres.py` | Ист./разовый? | — | P1 | — |
| `origin-ab-test.py` | Есть вызов | 1 | — | — |
| `p0-cross-source-dedup-impact.py` | Есть вызов | 1 | — | — |
| `p1-production-audit.ps1` | Есть вызов | 1 | — | — |
| `p13-production-eligibility-impact.py` | Есть вызов | 1 | — | — |
| `p16-readonly-baseline.py` | Есть вызов | 1 | — | — |
| `p2-production-maintenance.ps1` | Есть вызов | 1 | P1 | — |
| `partition-verified-repair-evidence.ps1` | Есть вызов | 1 | — | — |
| `phase3-production-health.ps1` | Есть вызов | 2 | P1 | — |
| `plan_reg_s3_orphans.py` | Есть вызов | 2 | — | s3-different-destruction-policy |
| `preflight_capacity_guard.py` | Есть вызов | 1 | — | — |
| `preflight_live_source_gate.py` | Есть вызов | 1 | — | — |
| `preflight_source_recovery.py` | Не установлен | — | — | — |
| `prepare-verified-gov-repairs.ps1` | Не установлен | — | — | — |
| `prepare-verified-tbankrot-repairs.ps1` | Не установлен | — | — | — |
| `prepare_gov_evidence_batches.py` | Не установлен | — | — | — |
| `prepare_ingestion_gov_journal.py` | Не установлен | — | — | — |
| `prepare_verified_gov_repair.py` | Есть вызов | 1 | — | — |
| `prepare_verified_tbankrot_repair.py` | Есть вызов | 1 | — | — |
| `preview-reported-lot-repair.ps1` | Не установлен | — | — | — |
| `probe-alfalot-readonly.py` | Есть вызов | — | — | — |
| `probe_sberbank_ast_live_raw.py` | Не установлен | — | — | — |
| `probe_utpl_readonly.py` | Есть вызов | — | — | — |
| `profile-gis-ingestion.py` | Не установлен | — | — | — |
| `property_address_impact_probe.py` | Есть вызов | 1 | — | — |
| `register-deleted-source-exclusions.ps1` | Не установлен | — | P1 | — |
| `regru-api-watchdog.sh` | Не установлен | — | — | — |
| `repair-closed-projection-invariants.ps1` | Не установлен | — | P1 | source-verified-repair-wrappers |
| `repair_closed_projection_invariants.py` | Не установлен | — | P1 | source-verified-repair-wrappers |
| `reports-source-evidence.py` | Не установлен | — | — | — |
| `resume-partial-standard-geo-map-no-vpn.ps1` | Не установлен | — | — | — |
| `run-all-etp-no-vpn-map.ps1` | Не установлен | — | P1 | — |
| `run-etp-failed-card-structure-no-vpn.ps1` | Не установлен | — | — | — |
| `run-etp-nistp-cdtrf-detail-qa-no-vpn.ps1` | Не установлен | — | — | — |
| `run-etp-off-vpn-map-campaign.ps1` | Не установлен | — | P1 | — |
| `run-geo-p0-readonly.ps1` | Есть вызов | 1 | — | — |
| `run-mets-full-readonly-no-vpn.ps1` | Не установлен | — | P1 | — |
| `run-nistp-full-readonly-no-vpn.ps1` | Не установлен | — | P1 | — |
| `run-nistp-high-lot-and-cdtrf-evidence-no-vpn.ps1` | Не установлен | — | — | — |
| `run-readonly-postrun-audit-home.ps1` | Есть вызов | — | — | — |
| `run-reg-s3-bounded-cleanup.ps1` | Не установлен | — | P1 | — |
| `run-standard-sources-geo-map-no-vpn.ps1` | Не установлен | — | P1 | — |
| `ruson_readonly_audit.py` | Есть вызов | 1 | — | — |
| `s3_capacity_probe.py` | Есть вызов | 1 | — | — |
| `source_transport_diagnostics.py` | Есть вызов | — | — | — |
| `systemic_database_audit.py` | Не установлен | — | — | — |
| `torgi_gov_detail_probe.py` | Есть вызов | 1 | — | — |
| `verify-etp-canary-acceptance.ps1` | Есть вызов | 2 | — | — |
| `verify-geocoder-no-vpn.ps1` | Не установлен | — | — | — |
| `verify-photon.ps1` | Не установлен | — | — | — |
| `verify_gov_exports_readonly.py` | Не установлен | — | — | — |
| `verify_postgres.py` | Не установлен | — | — | — |
| `verify_stale_nspd_readonly.py` | Не установлен | — | — | — |
| `verify_tbankrot_candidates_readonly.py` | Не установлен | — | — | — |
| `verify_torgi_gov_database_readonly.py` | Не установлен | — | — | — |

---

**Контроль:** `57` workflow, `114` scripts, всего `171` строк матрицы. Основа `main` `bbebd605`; результаты нельзя автоматически переносить на более поздние коммиты без повторного аудита.
