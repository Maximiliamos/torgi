# TORGI / Sterdez.online — единый ROADMAP разработки и внедрения ЭТП

**ТЕСТЫ обновлённой интеграции:** [Production reliability run 42532420382](https://github.com/Maximiliamos/torgi/actions/runs/42532420382) на кодовом SHA `eaec0e19d00b5167ba1e6e08adfda130badf55a3` завершился успешно: **9/9 проверок, SQLite 1 472 passed / 11 deselected**, PostgreSQL, WEB, lint/types, миграции, Windows и оба Docker smoke. REG.RU S3 diagnostics 42532420330 тоже success, но это проверка CI, не proof публичного manifest 8 ноября. Последний документационный commit проверяется отдельно.

**Фоновый GEO-статус Home:** [Phase 3 Lite 42530629088](https://github.com/Maximiliamos/torgi/actions/runs/42530629088) от 8 Nov показал `geo_percent=61.3`, `geo_actionable_remaining=9`, `geo_deferred_no_match=45408`, `geo_deferred_validation=1836`, `geo_network_wait=3`, `geo_external_network_degraded=false` и одну текущую MapDataset; статистика по всей системе, **не доказательство GEO 87 новых лотов**.


## 09.11.2026 — завершающий аудит 8 ноября и единая интеграция PR #1162

**RUN:** `f572bdff-3db1-41bf-94a4-1f75ab690232`, 8 ноября 21:00 МСК.
Подтверждено непосредственно операторским SQL: `partial`, **6 success /
1 Torgi Russia partial**, 87 новых, 6 235 обновлённых, 1 383
архивированных. Архивы: BidExpert 1 332, ГИС 49, Lot-online 2;
причины **НЕ ПОДТВЕРЖДЕНЫ**. M-ETS 153 страницы / 1 seen, тогда как
7 ноября было 155 / 1 191; это критичная аномалия приёмки, не
полный success. CDTRF 32 / 1 595 (17 inserts, 1 578 updates), NISTP
143 / 202 (3 inserts, 3 updates). Все три новые ЭТП имеют
`complete_source_run=false` и не имеют архивирования отсутствующих лотов.

Карта: в PostgreSQL `20261108T215500614188Z-r6-bundle-s3`,
73 488 точек; public S3 manifest, новые GEO-координаты и WEB-маркеры
**ещё не проверены независимо**.

- [x] **КОД (PR #1162):** консолидирована ветка #1161, восстановлена
  прежняя восьмикатегорийная область M-ETS и CDTRF land, не
  менялся BidExpert `all`, запрещён кэш деталей по `last_seen_at`.
- [x] **ДИАГНОСТИКА:** добавлен SELECT-only операторский отчёт
  `scripts/audit_20261108_nationwide_readonly.py` с причинными
  группировками архивов по временному окну и историей полных прогонов.
- [ ] **ТЕСТ:** дождаться полного зелёного CI на финальном SHA #1162.
- [ ] **АУДИТ HOME:** выполнить exporter, исследовать 1 332/49/2
  архивирований, несоответствие M-ETS 1 против 1 191, CDTRF
  повторные UPDATE и НИСТП спорные статусы.
- [ ] **GEO/S3/WEB:** отдельно подтвердить источник каждой новой метки.
- [ ] **MERGE:** #1162, только при зелёном CI + безопасном source
  audit. #1160/#1161 не объединять поверх друг друга.
- [ ] **DEPLOY/HOME:** exact-SHA Home/WEB/Cloudflare после проверки
  отсутствия активного sync lease и backup/restore evidence.
- [ ] **SOURCE ACCEPTED:** 7/7 не подтверждено.

Подробный handoff:
`docs/ETP_SEVEN_SOURCE_RUN_2026-11-08_FINAL_AUDIT.md`.


## 07.11.2026 — итог первого full-selected production run после PR #1130

- [x] Run `7810df7b-fb90-4c9f-9039-546ed151172b` дошёл до всех семи
  источников: МЭТС 155 страниц/1,191 seen/1,187 inserts; ЦДТ 32/1,588/
  309 inserts/1,279 updates; НИСТП 144/815/798 inserts/17 updates. Для
  этих трёх источников archival=0.
- [x] BidExpert завершил 1,450 страниц и 6,353 seen. ГИС Торги и Lot-online
  получили сетевые timeout после возврата VPN; «Торги России» подтвердили
  исправленную пагинацию/регион, но PostgreSQL отклонил structured
  `auction_type` при batch upsert.
- [x] Общий run завершился `failed` уже после source walks: монолитный SELECT
  всех 200k+ широких `processed_lots` в cross-source dedupe превысил
  `statement_timeout`. MapDataset `20261106T224400098846Z-r6-bundle-s3`
  опубликован раньше окончания run и не является финальным доказательством.
- [x] Follow-up исправляет три выявленных production-дефекта: PK-window
  дедупликацию по 1,000 строк, строковую нормализацию structured trade type и
  acceptance неархивирующих full-selected sources по successful source-end.
- [ ] После merge/deploy выполнить только необходимые повторные доказательства,
  затем GEO drain и свежий MapDataset/S3 verification. Не объявлять текущий
  failed run полной production-приёмкой.

## 07.11.2026 — разбор production run и переход от 5-page к full selected catalogue

- [x] PostgreSQL независимо подтвердил run
  `26ca1925-068f-4d86-ad7b-53d6482f5c21`: `partial`, шесть success и
  fail-closed `torgi-russia.ru`; 1,899 inserts, 6,068 updates и 35 архивов.
  Все 35 архивов относятся только к `torgi.gov.ru` и имеют причину
  `missing_after_two_complete_syncs`. Более поздних nationwide runs нет.
- [x] Фоновый map worker после импорта опубликовал current ready dataset
  `20261106T212400126715Z-r6-bundle-s3`: 67,991 points, 81,655 tiles,
  `published_at=2026-11-06 21:25:17`. Публичная S3-проверка из текущей
  VPN-сети зависает и должна быть повторена без VPN; старый partial-resume
  запрещает повтор после уже существующего более нового MapDataset.
- [x] Причина `torgi-russia.ru` найдена: live SSR records содержат регион в
  `region.title`, а guard проверял только legacy `region_title`. Нормализатор
  уже сохранял эти 23 строки как код `01`, поэтому исправляется только форма
  доказательства региона; проверка несовпадения остаётся fail-closed.
- [x] Штатные МЭТС/ЦДТ/НИСТП specs переведены с пяти страниц на полный
  доказанный выбранный каталог: `max_batches=500`, `require_source_end=True`,
  `reconcile_missing=False`. NISTP больше не ограничен двумя деталями/страницу;
  catalogue-only и terminal-leak строки по-прежнему не получают ложной
  индивидуальной приёмки.
- [x] ЦДТ использует `CdtrfSyncFilters(page_size=100)` с total/page/ID/detail
  guards. МЭТС получил stateful full crawler с total, page size, cross-page ID,
  detail identity, catalogue/detail price и status checks. Никакой из трёх
  источников не получает missing-lot archival.
- [ ] Merge/точный Home-deploy нового PR; затем один no-VPN полный операторский
  прогон с restore-verified backup, всеми семью источниками, bounded GEO до
  исчерпания очереди и immutable MapDataset/S3 verification.
- [ ] После реального прогона записать страницы/seen/insert/update/fail каждого
  источника. Полный выбранный пагинатор не закрывает NISTP #1115/#1116 и не
  доказывает provider-wide M-ETS scope (категории 11/40 всё ещё вне фильтра).

## 06.11.2026 — Подключение МЭТС/ЦДТ/НИСТП к штатному 09:00/21:00, PR #1128

- [x] **Разработка:** три источника включены в `default_source_specs()`,
  используемый штатным Celery beat `06:00/18:00 UTC`. Для каждого
  `max_batches=5`, `reconcile_missing=False`.
- [x] **Безопасность NISTP:** не более двух проверок индивидуальных
  деталей на страницу; строгие исходный статус каталога,
  индивидуальный ID, начальная цена и статус детали. Строки
  каталога без собственной проверенной карточки **не записывать**.
- [x] **Безопасность МЭТС/ЦДТ:** сохранены два валидируемых detail
  на страницу и пятистраничное окно.
- [x] **Альфалот не подключать** до доказательства правильного
  `stateFilter_GD` вместо `SUPCOMING`.
- [ ] **PR #1128:** проверить новые интеграционные тесты, CI, merge
  SHA и точный Home-deploy; не считать локальный тест деплоем.
- [ ] **Эксплуатационная приёмка:** проверить, что
  `celery-beat` действительно запустил каждый из 3 источников,
  `LotSyncSourceRun` показывает ограничения, реальное число
  новых/обновлённых/архивированных лотов и ошибки.
- [ ] **Отдельные вопросы:** не путать `reconcile_missing=False` с
  полным запретом всех операций записи: общая дедупликация, закрытие
  по подтверждённому статусу/времени и map-dirty всё ещё существуют.
- [ ] **Production source acceptance** остаётся открытой:
  NISTP #1115/#1116, CDTRF полная выбранная область, M-ETS
  категории 11/40 и конечные GEO/WEB/S3/scheduler проверки.

Этап подключения на уровне кода ≠ факт запуска в 21:00:
до слияния PR #1128 действовало прежнее расписание из `main`.
Дальнейшая работа обязана фиксировать точный merge/deployed SHA
и результирующие DB-журналы, а не только зелёный CI.


M-ETS merge/deployment update 2026-11-06: PR
[#1124](https://github.com/Maximiliamos/torgi/pull/1124) merged to `main` as
`dfd82647a23a6d8878cbfa33c0fc14e0da724f50`. Exact-SHA Home deploy
[42258748126](https://github.com/Maximiliamos/torgi/actions/runs/42258748126),
post-merge reliability
[42258748412](https://github.com/Maximiliamos/torgi/actions/runs/42258748412),
REG.RU deploy/public WEB smoke
[42258748246](https://github.com/Maximiliamos/torgi/actions/runs/42258748246),
and Cloudflare edge deploy
[42258748168](https://github.com/Maximiliamos/torgi/actions/runs/42258748168)
all passed after no-VPN relay recovery. Trusted-Home post-deploy M-ETS run
[42259466314](https://github.com/Maximiliamos/torgi/actions/runs/42259466314)
verified five pages, 100 distinct catalogue IDs and 10/10 limited details with
zero rejects. The run made zero DB writes and did not reconcile, archive,
geocode or publish M-ETS lots. Thus parser deployment and selected-catalogue
readability are `PASS`; M-ETS production import/GEO/map/scheduling remain open.

Browser/read-only follow-up 2026-11-06: see
`ETP_METS_ALFALOT_SBER_BROWSER_2026-11-06.md`. M-ETS exact SHA `d1241be5`
completed two matching selected-filter walks: 155 pages, 3,083 unique IDs,
identical ID and catalogue-record digests, and three items on terminal page
155. Two exact detail cards also match identity/initial price/status.
Alfalot's selected `stateFilter_GD` is confirmed as «прием заявки»: page 2 has
20 lots, terminal page 3 has 2, and two property cards independently report
`Идет прием заявок` with their own prices. The raw client pagination request and
property-only scope remain missing. Sberbank-AST is still unfiltered: the
bankruptcy checkbox is unchecked and its informational landing is not a result
catalogue. No Alfalot/Sberbank two-pass walk or production acceptance follows
from this.

M-ETS implementation follow-up 2026-11-06: code commit `74eaa4a` adds a
strict full two-pass read-only runner with page-by-page progress, exact selected
category/status checks, sequential next-link validation, catalogue ID/price/
status digests and an incomplete-terminal-page proof. **29 focused tests pass**
with Ruff/mypy/PowerShell parsing clean. The full local repository run produced
1,232 pass / 12 skip and two unrelated environment failures: stale installed
distribution `0.1.0` versus project `0.3.10`, and an existing naive local-time
versus UTC lease assertion. A real no-VPN two-pass run is now `PASS` for the
selected filter at exact SHA `d1241be5`; the seven-category filter still omits
category 11 land and 40 agricultural
real estate. No DB/reconcile/archive/GEO/map action was enabled.

M-ETS live preflight follow-up: after fixing the Windows checkout import in
merged #1123, SHA `9f75b567` parsed pages 1/2 without VPN: 20 + 20 items,
40 unique IDs, total 3,057 and next targets 2/3. The provider emits two
agreeing paginator blocks; PR #1124 now validates both rather than requiring
one global pager. This was an intentional two-page budget stop, not a complete
walk. Main SHA `0029c004` deployed successfully on Home in run 42207409497,
including backup/migrations, public REG.RU S3 manifest and local API checks.

M-ETS full-attempt follow-up: SHA `ddb1763b` parsed 152 pages and 3,040
unique IDs without duplicates. Reported total remained 3,059, but full page
152 exposed no Next; the scanner failed closed because 19 items were unreached
and did not begin pass 2. Next action is a short hashed tail probe of pages
152/153/154, not another full walk and not a weaker terminal rule.

Tail probe `3b951b88` then found 20 distinct-hash items on each of pages
152/153/154 at total 3,082. Page 154 omitted Next prematurely. PR #1124 adds
fail-closed continuation from a full page only when the reported total proves
more rows remain; completion still requires exact total and an incomplete last
page. Probe 155/156 before rerunning both full passes.

Initial 155/156 probe observed HTTP 308 on page 156 but lost the already-fetched
155 summary when it aborted. The probe now persists per-page 200 evidence plus
sanitized redirect target/status; the full scanner still fails on redirects.

Repeat tail probe `1e3b9b91` established page 155 as the terminal page at
live total 3,083: three cards, no Next, agreeing active markers. Page 156
returned HTTP 308 to filtered page 155. The terminal contract is now proven
for that snapshot. At that point two complete matching passes were the next
required step; the final run below subsequently completed them.

Final M-ETS selected-filter proof: exact SHA `d1241be5` completed two
independent 155-page walks with 3,083 unique IDs in each. ID digest
`3c0cfe95...e2e01b` and price/status/title record digest
`cee582e5...61f6e1` match; terminal page 155 contained three items in both
passes. This closes selected-filter catalogue pagination, identity, catalogue
price and status proof. It does **not** close complete provider scope,
individual active-card validation, PostgreSQL, reconciliation/archival, GEO,
MapDataset/S3/WEB or 09:00/21:00 scheduling.

Checklist refreshed 2026-11-06: all 22 ETPs have an evidence-scoped row in
`ETP_PARSER_STAGE_CHECKLIST.md`, with newer NISTP/CDTRF reports kept distinct
from the 05 November bounded Home baseline for the other 20 sources.
Integration #1118 at `831814f` passed 10/10 CI checks and 221 local focused
tests, then merged to `main` as `837f22a`. Later M-ETS PR #1124 merged and
deployed as `dfd82647` with the successful exact-SHA runs listed above.
NISTP/CDTRF source acceptance blockers and M-ETS production import remain
pending. The 12-area coverage audit explicitly records
the unintegrated detailed machine-stage expansion from #1098 and outstanding
DB/GEO/map/09–21 proof. Matching CDTRF ID digests do not prove an atomic snapshot.

Integration update 2026-11-06: the complete #1105–#1117 stack is preserved in
`codex/etp-stack-integration-20261106` and includes current main without a merge
conflict. 115 focused NISTP/CDTRF tests and Ruff pass. Corrected the #1105
source_id type failure and later nullable diagnostic guards; focused mypy passes.
That integration was subsequently merged through #1118; #1115/#1116 and
downstream source acceptance remain open. No new source production
import/GEO/map was run by the evidence described here.

## 06.11.2026 — R7/NISTP + CDTRF: этап исправления парсеров и read-only QA выполнен, production acceptance открыт

**Текущий операционный срез**: [передача следующему агенту и полная хронология](ETP_NISTP_CDTRF_AGENT_HANDOFF_2026-11-06.md) и [ETP stage checklist](ETP_PARSER_STAGE_CHECKLIST.md). Исторический срез 02.11 ниже описывает ранее развернутый production, а не новые read-only PR. **Ни один из PR #1105–#1114 не был merged на момент проверки**; отсутствие свежего CI и exact-SHA deployment доказательств не означает успех.

| Веха | Наблюдаемый факт | Результат |
| --- | --- | --- |
| NISTP полный каталог | 2 × 142 страницы, 2 716 стабильных ID; но 43 терминальных статуса в active-filtered выдаче | Каталог обойдён, **scope заблокирован** [#1116](https://github.com/Maximiliamos/torgi/issues/1116) |
| NISTP индивидуальные лоты | Первоначально 20/30; после фиксов 4/10 прежних ошибок исправлены; новая выборка **9/10** | Парсер улучшен, но `502379:259` не имеет проверенной индивидуальной секции |
| NISTP высокие лоты | 7/7 точных строк официального каталога; **0/7** собственных URL, только `trade_view.php?trade_nid=502379`; общая карточка содержала 100 секций с максимумом №128 | `catalogue-only`, не выдавать за подтверждённые detail: [#1115](https://github.com/Maximiliamos/torgi/issues/1115) |
| CDTRF выбранный каталог | 2 × 64 страницы, 6 351 уникальный ID, стабильный digest, live drift | Выбранная часть каталога подтверждена, **не вся площадка** |
| CDTRF индивидуальные лоты | Изначально 23/30; новый live sample **10/10**, 2 HTML/Nuxt title flags с совпадающими ID/ценой | Фиксы работают на новой выборке; семь ранее проблемных ID сместились с прежних страниц |
| Offline/оператор | 80/80, затем 83/83 focused tests; отдельный source-form/link census 9/9 | Локально проверено на отдельных SHA, **не CI/merge/deploy** |
| Производственный допуск | Новые QA не писали в PostgreSQL; ни одна из работ не разрешала reconcile, archive, GEO, MapDataset | `source_accepted=false`, автоматический импорт/приёмку **не включать** |

**Статус задач:**
- **Завершено на этом этапе** — NISTP и CDTRF: диагностические и parser-fix PR, точные отказы без подмены ID, сохранённые причины, bounded samples, документирование ограничений.
- **P0 next** — инженерное ревью/CI и последовательная интеграция PR [#1105](https://github.com/Maximiliamos/torgi/pull/1105) → [#1108](https://github.com/Maximiliamos/torgi/pull/1108) → [#1109](https://github.com/Maximiliamos/torgi/pull/1109) → [#1110](https://github.com/Maximiliamos/torgi/pull/1110) → [#1111](https://github.com/Maximiliamos/torgi/pull/1111) → [#1112](https://github.com/Maximiliamos/torgi/pull/1112) → [#1113](https://github.com/Maximiliamos/torgi/pull/1113) → [#1114](https://github.com/Maximiliamos/torgi/pull/1114). **Не сливать автоматически** без проверки совместимости с `main`; source acceptance не включать при merge.
- **P1 NISTP** — выяснить 43 неактивных записи в отфильтрованном каталоге [#1116](https://github.com/Maximiliamos/torgi/issues/1116), изолировать семь каталог-only high-lots [#1115](https://github.com/Maximiliamos/torgi/issues/1115), перепроверить финальный active-scope контракт на двух полных циклах только после фикса.
- **P1 CDTRF** — ревью двух HTML/Nuxt расхождений, свежая выборка ≥30 разных категорий, доказательство полноты нужного каталога, backup→staging DB canary→GEO/map→P11/scheduler gates **после отдельного подтверждения владельца**.
- **P1 следующая ЭТП для разработки** — **CenterR**: реальные IDs vs боковые промоссылки, пагинация/последняя страница, лот/цена/статус/detail; далее **Sistematorg** (исторически только 18/3 361 цен), отдельно recovery **Торги России**. Не переносить зелёные оценки NISTP/CDTRF на эти источники.
- **P1 дорожный контракт** — выполнить единый `NormalizedLot` [#1021](https://github.com/Maximiliamos/torgi/issues/1021) после источниковых контрактов; сохранить Decimal/provenance/timezone/idempotency и запрет выдуманных цен.

**Agent handoff**: [обязательная последовательность с критерием завершения каждого шага](ETP_NISTP_CDTRF_AGENT_HANDOFF_2026-11-06.md). Этапы `implemented → tested → merged → deployed → production_accepted` должны оставаться раздельными. Не объявлять ЭТП «готовой в production» по зелёной выборке 10/10.

---

> **Исторический статус на 02.11.2026:** см.
> [`ETP_ROLLOUT_STATUS_2026-11-02.md`](ETP_ROLLOUT_STATUS_2026-11-02.md).
> Текущий этап — **R7-CDTRF production completion and acceptance**. Новый код
> развернут, exact-SHA P1/Public WEB/functional зелёные, но повторный полный
> CDTRF import → GEO → map и exact-SHA P11 ещё не завершены. Из 16 запускаемых
> новых профилей production-записи есть у 3, а успешный полный source-run
> последней общей кампании есть у 2. Исторические статусы ниже сохраняются как
> журнал решений и не заменяют актуальный срез.

## Отложенная задача R6-DATA-001: унификация данных всех парсеров (30.10.2026)

Запрос владельца закреплён как [GitHub #1021](https://github.com/Maximiliamos/torgi/issues/1021). К этой общей задаче вернуться **после работы с ЦДТ**; сейчас не расширять её на все ЭТП.

**Требуется:** единый контракт `NormalizedLot` для названия, начальной и текущей цены, срока заявок, времени торгов, вида продажи, статуса, кадастра, URL и source-ID; `Decimal` RUB вместо потери денежной точности в `float`; UTC + исходная таймзона, provenance, полнота и тесты на каждом источнике. Не выдумывать неизвестные даты, цены и статусы. Разделить объект, процедуру продажи и публикацию источника. Принять только после source-specific contract tests и сквозной проверки сохранения.

## История R7-CDTRF — отдельный безопасный пакет (30.10.2026)

Ветка `feat/cdtrf-catalog-timeline-20261030` добавляет строгое чтение отдельного времени торгов из реальной структуры `catalog-card__trade-info-item` и преобразование подтверждённых дат начала/окончания заявок, вида торгов, ID сообщения ЕФРСБ и расписания публичного предложения в `NormalizedLot`. Фикстуры основаны на предоставленном браузерном сохранении страницы каталога. Дата подведения итогов **не является** временем начала аукциона. Данные каталога не являются подтверждёнными лотами.

Этот абзац отражает состояние 30.10.2026. Текущий статус и оставшиеся критерии
приёмки перенесены в актуальный срез выше. Missing-lot archival по-прежнему
запрещён без доказанной полноты двух стабильных циклов.

---

**Версия:** 1.0 · **Дата аудита:** 29 октября 2026 г.  
**Репозиторий:** https://github.com/Maximiliamos/torgi  
**Текущая основная ветка на момент подготовки 0.3.10:** `986616c1` ([#1013](https://github.com/Maximiliamos/torgi/pull/1013) merged, 10/10 CI; exact-SHA Home/P11 0.3.9 выполняются). Последний **подтверждённый** GitHub Release: [`v0.3.8`](https://github.com/Maximiliamos/torgi/releases/tag/v0.3.8) на `b5200eaa` после успешного P11; сведения ниже о состоянии старых PR и release относятся к времени их записи и не являются текущим статусом.
**Назначение:** единый исполнимый план для разработки, CI, домашнего production-узла, PostgreSQL, картографического конвейера, REG.RU и Cloudflare. Документ является планом, а не отчётом о выполнении.

> **Бизнес-результат:** карта Sterdez.online показывает только **актуальные продажи недвижимости** с достоверными координатами и источниками; пользователь фильтрует несколько ЭТП; цены и статусы обновляются; дубли разных публикаций одной продажи объединяются, **разные продажи одного объекта не смешиваются**; журнал, избранное и действия пользователя сохраняются. Неподтверждённые источники не выдают фиктивные лоты.
>
> **Приоритеты (обновлено по поручению владельца 29.10.2026):** P0 — не допустить необратимого повреждения существующих данных, ложного слияния продаж и небезопасного релиза; P1 — достоверность карты/GEO, проверенные ЭТП, статус/цена и мониторинг; P2 — оптимизация, эксплуатация и дополнительные продуктовые функции. Новое поручение P0/P1 отменяет прежнее ограничение «новых P0 не вводить».



## S5 / ЦДТ — реальный результат повторной read-only проверки (30.10.2026)

- Повторный запуск [S5 #40983300526, attempt 2](https://github.com/Maximiliamos/torgi/actions/runs/40983300526) на accepted v0.3.9 прошёл все production-gates и зафиксировал `restore_verified_backup_available=true`. В обезличенном JSON **МЭТС: две подтверждённые карточки, без обнаруженных SQL-коллизий; ЦДТ: 0 подтверждённых в текущем первом окне**, S5=failed. **Никаких DB writes или импорта**.
- Причина, требующая следующей проверки: старый `etp_manual_import.collect_lots` читал лишь первую страницу ЦДТ и максимум 2 detail. По ранее подтверждённому Home five-page read-only аудиту ЦДТ имела 8 verified property details на 5 страницах. Поэтому `collect_lots` теперь выбирает не более 2 *подтверждённых* лотов из **до 5 страниц** при строгом лимите `max_detail_requests <= 2` на страницу. Отказанные карточки не принимаются; контролируются полные fingerprints кандидатов, отсутствие дублей между страницами, номера страниц и конец окна.
- **Ограничение:** это расширение **только ограниченного** окна чтения ЦДТ, не полный каталог, не S5 acceptance и не разрешение `--apply`. До реальной проверки на новом exact SHA нельзя считать, что проблема ЦДТ устранена: возможны и другие ошибки/кадастровые коллизии. Предварительно проверенные партии могут измениться до операторского подтверждения, поэтому обязателен повторный identity/SQL guard в транзакции.

## S5 — устранение гонки при проверке готовности (30.10.2026)

- P11 для `v0.3.9` `986616c1` **пройден**, опубликована [v0.3.9](https://github.com/Maximiliamos/torgi/releases/tag/v0.3.9). Это снимает историческую блокировку выпуска `0.3.10`, но новая версия обязана пройти свою exact-SHA приёмку.
- [S5 Home run #40983300526](https://github.com/Maximiliamos/torgi/actions/runs/40983300526) **failed**: `Public WEB smoke has no successful latest exact-SHA run`. В тот момент последним был `skipped` WEB smoke; позднее [WEB #41014304441](https://github.com/Maximiliamos/torgi/actions/runs/41014304441) завершился success, но S5 не повторился автоматически. Не считать этот отказ доказательством отсутствия backup или поломки ЭТП: до этих проверок сценарий не дошёл.
- `etp-s5-canary-readiness.yml` теперь запускает **одну и ту же строгую read-only проверку** после каждого successful `P11`, `Public WEB smoke` или `Production functional reliability` на `main` с exact SHA. Все восемь production gates, restore-verified backup + SHA256, worker image и 1–2 реальные детали МЭТС/ЦДТ с rollback-only SQL collision checks **остаются обязательными**; до их успеха S5 fail closed. Никаких `--apply`, DB writes, GEO/Map publication.
- **До приёмки:** новый CI/S3 на интегрированном `0.3.10` SHA, merge, exact-SHA Home/P11 и **отдельный успешный S5 run**, выдавший отчёт с `restore_verified_backup_available=true` и без коллизий. Даже при S5 success полноценный операторский canary ещё не выполнен.

## 0.3.10 — общий staging P13/P2 (30.10.2026)

- Интеграционный head объединяет [#1014](https://github.com/Maximiliamos/torgi/pull/1014), [#1015](https://github.com/Maximiliamos/torgi/pull/1015) и [#1016](https://github.com/Maximiliamos/torgi/pull/1016) на единой версии `0.3.10` для одного полного CI/S3 и последующей **одной** exact-SHA Home/P11 приёмки. До релиза v0.3.9 не сливать.
- Проверка статус/возраст MapDataset и rollback перед каждым chunk, классификация direct SourceLot blockers и офлайн S3 inventory имеют отдельные тесты. **Ни один из этих PR не разрешает массовый DB backfill, удаление объектов S3, преждевременную публикацию MapDataset или автоматический импорт ЭТП.**

## P13 — диагностика прямого происхождения пропущенных регионов (30.10.2026; PR/CI)

- **Проверенный Home [run #40979232788](https://github.com/Maximiliamos/torgi/actions/runs/40979232788)** на SHA `986616c1` успешно изучил 50 исторических объектов в read-only. По прежней версии отчёта **у всех 50** отсутствовало *достаточное свежее прямое official SourceLot region evidence*; это не утверждение, что во всей популяции нет `SourceLot`.
- Новый диагностический `direct_source_blocker` для тех же **не более 50** объектов отдельно разделяет: `no_direct_source_lot`, `direct_source_unapproved`, `direct_official_source_inactive_or_archived`, `direct_official_source_stale`, `direct_official_status_unverified`, `direct_official_region_missing` и `direct_official_region_proof_present`. Это позволит точно установить, почему в предыдущем отчёте нет подходящих регионов.
- Запросы ограничены проверенными `processed_lot_id`; никакие `CanonicalLot`-соседи не считаются той же продажей. Отчёт содержит лишь агрегаты категорий и внутренние ID, не содержит кадастра, URL, адреса, должника или иных персональных данных. **Нет backfill, SQL writes, архивации, GEO и публикации карты.**
- **До приёмки** нужно подтвердить CI, затем объединить и выполнить Home exact-SHA workflow заново; только тогда результаты категорий можно учитывать при адресном восстановлении региона, с подтверждённым backup/rollback и операторским разбором. `3908` кандидатов со старой оценкой region unknown не считаются исправленными.

## P2 — безопасная офлайн-инвентаризация REG.RU S3 (30.10.2026; staging)

- Из [старого PR #959](https://github.com/Maximiliamos/torgi/pull/959) выборочно перенесены `scripts/plan_reg_s3_orphans.py`, тесты и runbook. Добавлены обязательные `IsTruncated=false`, запрет `NextContinuationToken`, проверка всех immutable-объектов, осмысленного возраста и часового пояса.
- Планировщик не подключается к S3 самостоятельно, **не удаляет файлы** и не импортирует в PostgreSQL. Для запуска нужен заранее собранный полный inventory и **каждый** dataset manifest; любое расхождение в ссылках — отказ. Даже корректный план не является одобрением удаления: сохранить CDN/rollback/live и сделать независимую проверку S3.
- Статус: staging, без Home/S3 production приёмки и доказанного освобождения места. Никакие 212 тыс. объектов из исторической оценки не удалялись этим PR.

## 0.3.10 — staged P2 ограниченное удаление старых тайлов карты (30.10.2026)

- Основной проект: `main` `986616c1`, 0.3.9 объединён после 10/10 CI, exact-SHA Home/P11 **ещё требует итогового подтверждения**. Последний опубликованный релиз 0.3.8 (`b5200eaa`), P11 success. МЭТС/ЦДТ S5–S7 не подтверждены, их реальные `ProcessedLot/SourceLot` в последнем P16 были 0.
- [Исторический PR #957](https://github.com/Maximiliamos/torgi/pull/957) проверен и точечно перенесён в новую branch `fix/p2-bounded-map-retention-20261030` для отдельного полного CI. `cleanup_map_datasets` ограничивает удаление `map_tiles` 1 000 строк/операция, максимум 100 000 строк/прогон; отдельные commit и продолжение в следующем запуске. `deleted_versions` включает **только действительно удалённые dataset**, поэтому их immutable S3 manifests не удаляются преждевременно.
- **Дополнительная гарантия этой ветки**: перед каждым SQL batch заново проверяется `MapDataset.is_current`, версия, `status`, возраст и список rollback-protected ready, под lock. Если после прошлого пакета dataset перестал быть допустимым кандидатом (например, восстановлена активная `building`), дальнейшее удаление прекращается. SQLite regression воспроизводит изменение статуса между двумя пакетами.
- **Важно:** это реальная функция очистки, но она не должна активироваться на Home до успешного CI + exact-SHA production release и проверки необходимости обслуживания; сохранение rollback-копий и S3 manifest обязательно. Цель не требует уничтожать историю лотов. Без независимой проверки безопасного хранения S3 P2 нельзя считать полностью принятым.
- Статус на момент записи: **PR/staging**, Home-каталог не изменён.

## Подтверждённая приёмка 0.3.8 и ЭТП (30.10.2026)

- **main `b5200eaa`**: production CI, Home deploy, REG.RU WEB/API, Cloudflare edge, публичный WEB и functional smoke — success; **P1 production data quality** также success; **P11 30-min soak ещё выполняется**. `v0.3.8` **не опубликован** на момент этой записи; latest GitHub release — `v0.3.7`. Не сливать 0.3.9 с обходом exact-SHA P11.
- [Home M-ETS run #40970926040](https://github.com/Maximiliamos/torgi/actions/runs/40970926040) success: 5 страниц, 100 уникальных ID каталога, 100 candidate fingerprints, 10 проверенных property details, 0 отклонений, без DB writes.
- [Home CDTRF run #40970926049](https://github.com/Maximiliamos/torgi/actions/runs/40970926049) success: 5 страниц, 100 ID общего каталога; **48** кандидатов недвижимости, 48 candidate fingerprints, 8 подтверждённых property details, 2 отклонения. Это **не** 100 подтверждённых объектов недвижимости.
- [Home P16 run #40970926059](https://github.com/Maximiliamos/torgi/actions/runs/40970926059) success: current MapDataset 52 028, строгий P13 максимум 4 149 (**7,97%**), 48 555 cross-source merged pairs для проверки, МЭТС/ЦДТ SourceLot=0/ProcessedLot=0/GEO=0. Не публиковать предложенную карту и не путать suspicious pairs с доказанными ошибками.
- [PR #1013](https://github.com/Maximiliamos/torgi/pull/1013) staging 0.3.9 содержит только read-only P13 region review и S5 readiness, дополненные SQL проверкой существующих SourceLot/ProcessedLot кадастровых и exact-ID конфликтов **до** канареечного импорта. Это дополнительная защита, не отменяющая проверку непосредственно перед commit. **S5 persisted lots по-прежнему 0 до отдельной приёмки.**

### Защита в ручном workflow

- `.github/workflows/etp-manual-verified-import.yml` теперь **до SQL UPDATE/INSERT** запускает `etp_manual_import --audit-collisions` на двух свежих карточках и требует 0 уже существующих cross-source / source-only / exact-ID конфликтов. Стандартные independent exact-SHA P11 и restore-verified backup гейты не ослаблены.
- В самом `run_import(..., apply=True)` перед транзакцией дополнительно проверяется уникальность кадастровых номеров **внутри** 2-лотной партии, чтобы source drift между проверкой и импортом не создал необоснованное дублирование. Если кадастровые номера совпали — отказ, операторская проверка, без DB writes.
- Настоящий canary импорт остаётся **ручным**; цена/status/GEO/map должны быть проверены по exact persisted IDs, прежде чем объявлять ETP source S5–S7.

### Проверка непосредственно перед записью (0.3.9 staging)

- `etp_canary_persistence.persist_canary_lot` теперь **внутри транзакции** повторно проверяет `SourceLot` другой площадки с тем же кадастром, включая те записи, которые ещё не связаны с `ProcessedLot`. Ранее эта проверка была только в предварительном SQL просмотре, а между ним и commit возможны изменения.
- В момент записи также запрещено превращать уже существующий `ProcessedLot/SourceLot` с точным `source_system + external_id` в «первый новый canary». Это только строгий **первый** пробный импорт, не плановый инкрементальный update старого лота.
- SQLite regression защищают оба сценария без вызова `persist_lot`. Это не обеспечивает глобальной serializable-защиты от всех гонок: PostgreSQL UniqueConstraints и откат при исключении остаются дополнительными рубежами. Production-импорт не подтверждён.

## S5 pre-import collision safety (staging, 30.10.2026)

- `src/bankrotai/services/etp_preimport_collision_review.py`: ограниченная до **двух** проверенных деталей read-only проверка существующих `ProcessedLot` и `SourceLot` по отдельным индексированным ID и кадастру. Даже совпадение кадастра другой ЭТП является **причиной ручного разбора**, а не доказательством одной продажи.
- `etp_manual_import --audit-collisions` запрещено сочетать с `--apply`. PostgreSQL-сессия выполняет только SELECT и rollback. В sanitized отчёте лишь агрегированные counts: cross-source cadastral collision, old source/processed ID, duplicate cadastral within batch, new no-known-collision.
- Readiness-проверка #1013 требует **нулевых** конфликтов для обеих первых двух карточек каждой площадки; при конфликте — fail closed, оператор должен выбрать иную проверенную партию. Даже результат `ready_for_separate_operator_canary=true` **не гарантирует отсутствие гонок**, ни права автоматического импорта/архивирования или немедленной публикации карты: все source/DB/GEO/WEB-gates остаются обязательными.
- **Статус:** staging, до принятого release 0.3.8 и интеграционного CI/реального Home preflight не считается production-функцией.

## 0.3.9 — staging интеграция подтверждения регионов и readiness ЭТП

- [#1011](https://github.com/Maximiliamos/torgi/pull/1011) и [#1012](https://github.com/Maximiliamos/torgi/pull/1012) интегрированы на базе 0.3.8 `b5200eaa` только для общего CI; обе функции **read-only**.
- **До слияния/релиза**: завершить exact-SHA Home/P11 релиза 0.3.8, пройти новый полный интеграционный CI + S3, затем после merge отдельно проверить 50 образцов `region_code` и canary readiness. Это **не исправление 3908 регионов**, не S5 импорт и не публикация исправленной карты.

## P13/P15 — исследование исторически пустых регионов (staging)

- [Issue #1010](https://github.com/Maximiliamos/torgi/issues/1010): по exact-SHA Home P13 report 3 908 из 4 149 предварительно подходящих лотов имеют `region_code` NULL/пустой. Эта цифра описывает **полный P13 preview**, а не следующую диагностическую выборку.
- Новый `bankrotai.services.historical_region_review` формирует **не более 50** read-only примеров действующих primary-лотов с кадастровым GEO и пропущенным регионом. Только прямые свежие официальные `SourceLot` + одиночный структурно корректный кадастр могут дать `consistent_requires_manual_approval`; конфликт, старый или отсутствующий источник остаются `blocked` для backfill.
- Workflow `p13-historical-region-review.yml` принимает только exact Home SHA, сохраняет обезличенный JSON (внутренние ID/региональные коды, без адресов и номера кадастра). **Нет SQL UPDATE, GEO-публикации, архивирования или полного исправления 3 908 записей**; это sampling, не весь P13-список.
- Следующая стадия после release acceptance: независимый impact/lineage review и restore-verified backup; только затем обратимый операторский батч и повторный P13 + фильтры региона + WEB.

## R7/S5 — zero-write canary readiness gate (staging 30.10.2026)

- До ручного импорта МЭТС и ЦДТ на принятом exact SHA нужен **отдельный** read-only сигнал о готовности: реальный восстановленный PostgreSQL backup за последние 60 ч, актуальный `main`/P11/WEB/REG.RU/Cloudflare, правильная версия ingestion worker и 1–2 заново проверенных property details каждого источника.
- `scripts/etp-canary-readiness.ps1` повторно использует обязательный `verify-etp-canary-acceptance.ps1` и сравнивает SHA256 backup с manifest; запускает `etp_manual_import` **без `--apply`**. Артефакт содержит только source ID, количество проверенных деталей и причины блокировки, не external IDs/ФИО/адреса.
- `.github/workflows/etp-s5-canary-readiness.yml` запускается **только после success P11 main** на exact SHA, ничего не записывает и завершает failure при ошибках. Сигнал `details_verified_manual_import_still_required` **не даёт разрешения автоматически импортировать**: проверка кадастровых конфликтов, сам импорт 1–2 лотов, GEO, journal, MapDataset, S3 и WEB — последующие отдельные операторские действия.
- **Статус:** staging PR, пока без merged/Home доказательств; 0 новых S5–S7 источников не сертифицировано. Версия 0.3.8 `b5200eaa` ещё должна пройти P11 и публикацию release.

## R7 / 0.3.8 — совместная проверка новой ветки

- [PR #1007](https://github.com/Maximiliamos/torgi/pull/1007) синхронизирован с **актуальным** `main` после #1008/#1009; `src/bankrotai/services/etp_fivepage_readonly.py` проверяет все кандидаты пяти страниц МЭТС/ЦДТ по хешам ID, включая те, чьи детали не были загружены из-за лимитов.
- При повторении ID на **несоседних** страницах или отсутствии полного списка fingerprint происходит fail-closed. Доказательства S2/S3 не могут означать S5/S6/S7; `reconcile_missing=False`, production PostgreSQL пока не заполнен.
- На Home после **успешного** exact-SHA deploy два ограниченных read-only workflow `etp-mets-pagination-readonly.yml` и `etp-cdtrf-pagination-readonly.yml` теперь отдельно запускают `etp_fivepage_readonly` (не более 5 страниц и 2 details/страницу), проверяют полный список candidate fingerprints и сохраняют JSON. Ошибку HTML/API/повторных ID нельзя маскировать как завершённый source sync.
- **Обязательные ворота:** новый 10/10 CI/S3 на интеграционном head, затем merge и полный exact-SHA Home/P11/WEB/functional; финальный release только после доказательств. Отдельная проблема пропущенных исторических `region_code`: [#1010](https://github.com/Maximiliamos/torgi/issues/1010).

## Фактические результаты P13/P16 после PR #1008 (30.10.2026)

- [#1008](https://github.com/Maximiliamos/torgi/pull/1008) **merged** в `main`, SHA `53c2c62ba95a322959a8fccefb00280f78b0d496`; 10/10 CI/S3 success на PR-head; [Home P16 run #40967272342](https://github.com/Maximiliamos/torgi/actions/runs/40967272342) **success** на exact merge SHA после устранения PostgreSQL `statement_timeout`.
- Из Home лога: 53 304 mapped/unarchived primary, 52 672 candidate problem rows; текущий MapDataset **52 028 точек**; P13 **4 149 кандидатов**, ratio **7,97%**. Это верхняя граница, не число безопасных точек. Существенное улучшение диагностики против прежних 639, но карта **не опубликована**, coverage gate остаётся блокером.
- P0: **48 555 подозрительных cross-source пар**, не подтверждённых ошибочных merges; МЭТС и ЦДТ **0 SourceLot, 0 ProcessedLot, 0 GEO/events** на момент запуска. Сквозная ЭТП-приёмка всё ещё **не выполнена**.
- В этой отдельной P0-ветке готовится ограниченная очередь до 20 примеров на уровень риска с ID и названием источника без ФИО/адресов/документов. Только операторская классификация по первоисточникам; никаких автоматических split/DB writes. Результат считать **готовым** только после CI, merge и Home отчёта.
- Общая оценка выполнения ROADMAP остаётся приблизительной: цель 50% требует критических E2E этапов. Нельзя повышать процент по одному зелёному мониторингу.

## Обновление 30.10.2026 — движение к 50% (не приёмка)

- **Актуальный main перед этой веткой:** `4b995050`, [#1005](https://github.com/Maximiliamos/torgi/pull/1005) merged; `v0.3.6` выпущена на `adad8cd8` (SBOM/SHA256SUMS). `0.3.7` объединена, но exact-SHA P11 на момент проверки ещё выполнялся, отдельный release не подтверждён.
- **R1/R4 блокер:** [P16 Home run #40962180271](https://github.com/Maximiliamos/torgi/actions/runs/40962180271) failed: `psycopg.errors.QueryCanceled` / PostgreSQL statement timeout в `scripts/p13-production-eligibility-impact.py` на коррелированном `SourceLot JOIN CanonicalLot` с `OR` по двум FK.
- **Доработка в [PR #1008](https://github.com/Maximiliamos/torgi/pull/1008):** разделены две независимые индексируемые SQL EXISTS-ветки (direct/legacy), те же source/status/freshness guards; добавлены тесты SQL-формы и legacy-only. **Статус: PR/CI, НЕ production accepted**, пока exact-SHA Home P16 не пройдёт без тайм-аута и не получены актуальные цифры покрытия карты. Никаких DB writes или map publication.
- **R7:** [#1007](https://github.com/Maximiliamos/torgi/pull/1007) остаётся открытым 0.3.8 staging с проверкой всех каталожных ID на пяти страницах, а не только выбранных для detail. Это не полный каталог и не production sync.
- **Точка отсчёта готовности:** ранее дана **ориентировочная экспертная оценка ~40% ±5–10 п.п.**, не вычисляемая по чеклисту. Цель 50% нельзя засчитать по созданным PR; необходимы зелёные CI, точная Home-приёмка, реальный DB→GEO→Map/WEB proof и исправление критических блокеров. Новых production ETP S5–S7 не подтверждено.

## 0. Живой журнал выполнения P0/P1 и ЭТП (29.10.2026)

**Правило обновления:** после каждого действия сначала вносим проверяемую запись (дата, задача, PR, SHA, тесты и ограничение), затем изменяем статус `не начато → в работе → PR/CI → merged → deployed → production accepted`. Пункт с кодом, но без сквозной production-приёмки, не считается закрытым. Записи ниже различают историю и фактическое состояние `main`.

### Журнал собственных изменений ассистента

| Дата | Что сделано лично в GitHub | Доказательство | Чего это НЕ доказывает |
|---|---|---|---|
| 29.10.2026 | Выявлен блокер exact-SHA деплоя `bbaeff53`: [Home run #40936068752](https://github.com/Maximiliamos/torgi/actions/runs/40936068752) — timeout Docker builds 180/240 сек при apt/Chromium; overlay безопасно отказал при изменении `pyproject.toml`. Подготовлен [#1000](https://github.com/Maximiliamos/torgi/pull/1000) — ограниченные 900/900 сек с сохранением fail-closed проверок | PR #1000, новый регрессионный тест; CI ещё выполняется | Изменение пока в PR; Home/REG.RU/P11 `bbaeff53` завершились failure; v0.3.6 не выпущен, требуется новый exact-SHA деплой и приёмка |
| 29.10.2026 | **Объединил [#1004](https://github.com/Maximiliamos/torgi/pull/1004)**: ограниченный Home offline overlay при version-only изменении `pyproject.toml` и неизменных requirements/certs | [merge `adad8cd8`](https://github.com/Maximiliamos/torgi/commit/adad8cd808266e9fd670b6493fc6cfb3fc6a6763), 9/9 CI и S3 diagnostics; прежний Home `dcf52af8` failure [40939341656](https://github.com/Maximiliamos/torgi/actions/runs/40939341656) | Новый Home/P11/REG.RU/Cloudflare `adad8cd8` ещё не принят, v0.3.6 не выпущен |
| 29.10.2026 | Подготовил **отдельную staging-ветку 0.3.7**, объединяющую [#1001](https://github.com/Maximiliamos/torgi/pull/1001) (2-лотный атомарный ETP canary + exact-ID audit), [#1002](https://github.com/Maximiliamos/torgi/pull/1002) (опциональный 5-страничный МЭТС/ЦДТ) и [#1003](https://github.com/Maximiliamos/torgi/pull/1003) (read-only P13 direct SourceLot provenance) | `release/0.3.7-etp-p13-staged-20261029`, исходные PR каждый 9/9 CI + S3; **совместный CI должен пройти отдельно** | Пока не merged и не deployed; S5–S7, историческая очистка, GEO/MapDataset E2E остаются открыты. Не сливать в main до immutable v0.3.6 release и новой exact-SHA приёмки |
| 29.10.2026 | **Объединил #993**: исправлены новые SourceLot timestamps/ProcessedLot region, GEO invalidation, 3 регрессионных теста | [PR #993](https://github.com/Maximiliamos/torgi/pull/993), merge [`a7586937`](https://github.com/Maximiliamos/torgi/commit/a7586937cced6d4b36fd756a8bf30f4c185fbfda), 9/9 CI | Не исправляет historical records; P11 exact нового SHA не принят |
| 29.10.2026 | **Объединил #994**, перенос P0 из устаревшего #987 на post-#993 main; старый #987 закрыл как заменённый | [PR #994](https://github.com/Maximiliamos/torgi/pull/994), merge [`e527fedf`](https://github.com/Maximiliamos/torgi/commit/e527fedf5994687a8bd97963fa69e08724d48b47), 9/9 CI | Никакой historical split/backfill; нет подтверждённой новой P11 / E2E |
| 29.10.2026 | **Объединил #996** — версия 0.3.6 в Python/WEB/Windows, v0.3.5 не изменён | [merge `49510a7e`](https://github.com/Maximiliamos/torgi/commit/49510a7e323fc028744d8861e8a0908995538070), 9/9 CI + REG.RU S3 diagnostics success | P11, WEB, functional и новый immutable release на 49510a ещё должны пройти |
| 29.10.2026 | Проверил Home read-only артефакты P0/P13/P16 и 22 ЭТП; зафиксировал причины блокировки массового импорта и очистки карты | [Аудит с числами и workflow-ссылками](production-readonly-audit-2026-10-29.md); 48 724 cross-source pairs, 7 430 conflicting cadastral, карта 51 705 vs P13 maximum 639, МЭТС/ЦДТ DB=0 | Категории потенциальных ошибок пересекаются; артефакт не доказывает ошибочность каждой пары и не разрешает SQL writes |
| 29.10.2026 | Объединил [#989](https://github.com/Maximiliamos/torgi/pull/989): единый ROADMAP, README, автоматический S0/S1 read-only survey 22 ЭТП и ограниченный S2 paging МЭТС/ЦДТ | [merge `dba8493b`](https://github.com/Maximiliamos/torgi/commit/dba8493b2d27ffcc809d74944eb3db5c18d03229), 9/9 CI, REG.RU S3 diagnostics success | Не импортировано 22 ЭТП и не подтверждены полные каталоги/реальные Home JSON; S5/S6/S7 открыты |
| 29.10.2026 | Объединил [#991](https://github.com/Maximiliamos/torgi/pull/991) — исправление классификации имущества ЦДТ по официальному `lotInfo`, отказ при аренде/долях/правах требования; старый #975 закрыл как заменённый | [merge `3171245b`](https://github.com/Maximiliamos/torgi/commit/3171245b72931f547c9d816f4a0e44abc697f867), 9/9 Production reliability CI | Это не доказательство реального повторного Home smoke, пробного импорта в PostgreSQL, корректности статусов и отображения на публичной карте |
| 29.10.2026 | Подготовил [#991](https://github.com/Maximiliamos/torgi/pull/991) поверх main `dba8493b`, перенёс исходные 5 файлов из #975 и повторно испытал | [PR #991](https://github.com/Maximiliamos/torgi/pull/991), итоги merge отдельно зафиксированы выше | Полные S5–S7, регулярный sync и корректный публичный результат остаются открыты |
| 29.10.2026 | Создал [PR #987](https://github.com/Maximiliamos/torgi/pull/987): source-specific default canonical identity, запрет автоматической дедупликации разных продаж по одному кадастру/цене/адресу, добавил тесты | [PR #987](https://github.com/Maximiliamos/torgi/pull/987), commit [`ddbd28ea`](https://github.com/Maximiliamos/torgi/commit/ddbd28eabb37fb7af9a7cb4dc9da31a7e2204691) | PR пока не принят в `main`, интеграционные CI/live regression ещё не подтверждены; старые ошибочные группы не исправлены |
| 29.10.2026 | Проверил CI и **объединил PR #986** (разделение конкурентных smoke по exact SHA; ожидание повторного проверенного запуска без fail-open). | [PR #986](https://github.com/Maximiliamos/torgi/pull/986), [merge `c0d0a040`](https://github.com/Maximiliamos/torgi/commit/c0d0a040bc8c4897b47811d57841588b26ce095a), 9/9 CI на PR head `5284d495` | Не подтверждены P11 нового SHA, успешный подписанный релиз 0.3.6, импорт ЭТП, исправление исторических записей и появление их на карте |
| 29.10.2026 | Обновил этот документ в ветке [PR #976](https://github.com/Maximiliamos/torgi/pull/976), сохранил предыдущие требования и незакрытые проверки. | Текущая редакция `docs/ROADMAP.md` в ветке PR #976 | ROADMAP остаётся **не в `main`** до отдельного слияния; правка документа не равна выкладке функций |


Эта секция уточняет статусы **на момент последней независимой проверки**. Не меняет требования разделов 1–14. `PR created`, `CI passed`, `merged`, `deployed`, `P11 accepted`, `E2E source accepted` — **разные** состояния.

| ID | Приоритет | Практический результат | Фактический статус на 29.10.2026 | Следующий proof |
|---|---|---|---|---|
| R6/BAT-002 | P0 | Разные продажи одного объекта должны оставаться отдельными | [#994](https://github.com/Maximiliamos/torgi/pull/994) **merged** `e527fedf` (9/9 CI), перенёс #987 на свежий post-#993 main; 48 724 подозрительных пары из live read-only аудита | P11 exact SHA, проверить влияние на существующие источники; **исторический split/backfill не выполнен**, исправлять только с verified restore и обратимым SQL |
| R3/P15 | P1 | Выбор только структурно подтверждённого номера объекта НСПД/PKK | PR [#980](https://github.com/Maximiliamos/torgi/pull/980) **merged**; SHA `c8572374`, P11 принят | P11 + реальная GEO-регрессия, impact report |
| R5/P17 | P1 | Exact Home SHA до REG.RU/Cloudflare, P11/functional release | `3171245b` полностью принят в production: Home/REG.RU/Cloudflare/P11/Public WEB/functional **success**; новый main `e527fedf` ещё не принят | Подтвердить все live gates **нового** SHA перед эксплуатационным signoff, затем immutable 0.3.6 |
| R7/CDTRF | P1 | Категория по HTML `lotInfo` и официальному badge; отказ при продаже прав/долей | [#991](https://github.com/Maximiliamos/torgi/pull/991) **merged** at [`3171245b`](https://github.com/Maximiliamos/torgi/commit/3171245b72931f547c9d816f4a0e44abc697f867), 9/9 CI; первоначальный #975 закрыт как заменённый | После merge повторить реальный Home read-only CDTRF detail и подтвердить S2; только после restore proof и P0 gate — controlled S5 canary, DB/GEO/Map/WEB |
| R8/S0–S1 | P1 | Read-only обследование 22 профилей | [22/22 Home audit](https://github.com/Maximiliamos/torgi/actions/runs/40925467730) **success**, 0 source certified for ingestion; МЭТС/ЦДТ bounded paging 5 страниц × 100 уникальных IDs | Полный каталог и proof S5/S6/S7; после backup + P0 пройти ручной canary импорт; не включать partial reconciliation |
| R1/P13 + R4/P16 | P0/P1 | Read-only impact real estate sale map, GEO, historic cross-source groups | [Home P16 baseline](https://github.com/Maximiliamos/torgi/actions/runs/40925467727) success. [Аудит](production-readonly-audit-2026-10-29.md): текущая карта 51 705; строгая P13 политика даёт максимум 639 (1,24%), статус stage минус 30 140, strict GEO минус 14 813, freshness минус 7 221. | **Не публиковать** карту только с 639 точками; восстановить real source status/geo/canonical по evidence; проверить 5+100 объектов и rollback |
| R11/RELEASE | P1 | Неизменяемые версии после exact SHA | `v0.3.5` опубликована ранее; **0.3.6 main `adad8cd8`** (merged #1004) ожидает Home/REG.RU/Cloudflare/P1/P11/Public WEB/functional, тег v0.3.6 ещё не принят; код **0.3.7 только staging**, не merged | Сначала successful exact-SHA `adad8cd8` + SBOM/SHA256 v0.3.6; потом совместный CI staging 0.3.7, отдельный deploy и immutable v0.3.7 |
| R11/SMOKE-GATE | P1 | Исправлен механизм конфликтующей отмены WEB/functional exact-SHA проверок и ожидания release gate | [#986](https://github.com/Maximiliamos/torgi/pull/986) **merged** в `main`, SHA [`c0d0a040`](https://github.com/Maximiliamos/torgi/commit/c0d0a040bc8c4897b47811d57841588b26ce095a); 9/9 CI пройдено **до merge**. Исходный сбой: [Actions #40908784706](https://github.com/Maximiliamos/torgi/actions/runs/40908784706) | Подтвердить P11, `Public WEB smoke`, `Production functional reliability` и успешный *новый* release на **точном `c0d0a040`**; до этого production закрытие НЕ засчитывать |
| R0/BAT | P2 | Все 23 старых BAT-описания и пользовательные P13–P17 ТЗ в одном файле | Канонический `docs/ROADMAP.md` **merged в `main`** через #989, старый PR #976 закрыт как заменённый | Обновлять журнал при каждом PR; для каждой задачи требовать SHA, CI, Home evidence и DoD |
| R7/CDTRF-S2 | P1 | Проверить до 5 страниц официального JSON API ЦДТ без повторных lot IDs | #984 включён в **merged #989**, код/CI приняты в main | Реальный Home read-only JSON с результатами нескольких страниц, затем проверка полной пагинации |
| R7/METS-S2 | P1 | Проверить до 5 страниц HTML каталога МЭТС без дубликатов | #985 включён в **merged #989**, код/CI приняты в main | Реальный Home read-only JSON, затем полный обход и controlled canary |


**ETP inventory classification:** 5 S1 candidates (`nistp.ru`, `etpu.ru`, `centerr.ru`, `zakazrf.ru`, `utpl.ru`), 2 existing connectors with readiness not assessed, 3 S0 blocked/parser errors, 5 S0 missing public catalogues, 7 S0 empty/changed HTML. **Ноль** новых S5–S7. См. [полную таблицу](production-readonly-audit-2026-10-29.md).

**Live evidence 29.10.2026:** [M-ETS/CDTRF Home probe](https://github.com/Maximiliamos/torgi/actions/runs/40925467810): МЭТС 20 кандидатов / 5 подтверждённых, ЦДТ 10 / 2 подтверждённых / 3 отклонённых; [read-only DB audit](https://github.com/Maximiliamos/torgi/actions/runs/40925467727) = **0** записей обеих ЭТП. Ни 22 profile-survey, ни пять страниц не доказывают S5–S7. См. [production audit](production-readonly-audit-2026-10-29.md).

**Следующий блокирующий порядок:** P0 исторические группы (impact/audit) → P17 production P11 → P13 impact + P15 реальный GEO → CDTRF/METS ручной S5 → scheduled incremental без archival → полное source-specific paging и остальные ЭТП. Все массовые DB writes только после restore-verified backup.


## 1. Источники задания и метод аудита

1. Пользовательское ТЗ «P13-P17.docx»: карта «продажа + недвижимость + активные торги», жизненный цикл источников, строгий кадастровый GEO, историческая очистка, проверка exact SHA и production-quality gates.
2. «Изменение и доработка блока поиска по кадастровому номеру.docx»: предварительная панель по кадастровому номеру, границы, подробная карточка, адресные подсказки и выбор результата, связь с торгами.
3. «Ошибки.docx»: 5 конкретных регрессионных кейсов (две аренды, мопед, завершённые торги, неверное кадастровое GEO).
4. «Что отстаёт.docx»: операционные runbooks, README, ROADMAP, legacy-развёртывания, ручной exact-SHA gate, вопросы source freshness и GitHub Issues.
5. Предыдущие ТЗ: 20+ прямых ЭТП, фильтр источников под «Субъекты РФ», связь PostgreSQL—дедупликация—журнал—GEO—карта, бот диагностического сбора HTML/JSON, независимость от зарубежных сервисов, GitFlic, производительность домашнего ПК, резервирование.
6. Проверены файлы основной ветки и текущие PR/Issues/Actions репозитория; приведённые ниже текущие статусы основаны на этой проверке. Статусы из исторических ТЗ **не считаются автоматически актуальными**.

Ссылки для технической проверки: [PR #932](https://github.com/Maximiliamos/torgi/pull/932), [PR #965](https://github.com/Maximiliamos/torgi/pull/965), [PR #975](https://github.com/Maximiliamos/torgi/pull/975), [ETP live smoke](https://github.com/Maximiliamos/torgi/actions/runs/40799133912), [backup+restore](https://github.com/Maximiliamos/torgi/actions/runs/40795626001), [P11 production acceptance](https://github.com/Maximiliamos/torgi/actions/runs/40798838152).

## 2. Проверенный статус — что сделано, что нет

| Компонент | Статус на 29.10.2026 | Что ещё требуется |
| --- | --- | --- |
| Основная эксплуатация Home / REG.RU / Cloudflare | **Работает**; основные deploy, health и P11 получили success на `f70f128e` | Пройти приёмку новых источников и строгой версии карты на собственном exact SHA |
| Backup PostgreSQL + isolated restore | **Выполнено и испытано**; 525 503 859 байт, restore passed | Продолжать мониторинг; проверить внешнюю зашифрованную копию и реальное восстановление вне домашнего диска |
| МЭТС | **Read-only пилот:** последний Home smoke #40908345635: 20 кандидатов, 5/5 проверенных деталей, DB writes=0 | Принять #989 S1/S2 paging, полный каталог, controlled import, S5 GEO/журнал/S3/WEB и регулярный sync |
| ЦДТ | **Read-only пилот:** последний Home smoke #40908345635: 11 кандидатов, 3/5 деталей; 2 отклонены `cadastral_missing`, DB writes=0 | Проверить #975 и #989 по живым карточкам/полной пагинации без ослабления identity/price/status; затем controlled import |
| PR #975 | **Открыт**, CI Production reliability и S3 diagnostics успешны | Merge после code review и live smoke; текущий `main` всё ещё со старой классификацией |
| 14 пилотных профилей | **Read-only** preview/candidate extraction | Для каждого полноценный безопасный `search()`, detail, пагинация, статусы и точная приёмка |
| 22 профиля в матрице | **Описаны** в `direct_etp_catalogue.py` | Включить новые безопасные источники; Lot-online не дублировать |
| `etp_incremental_source_specs()` | **Есть**, но отдельный opt-in: 2 страницы, `reconcile_missing=False` | Доказать полный каталог, ввести staged regular sync |
| Ручной импорт ЭТП | **Код/защита есть**, workflow только `workflow_dispatch`, максимум 2 детали | Доказать живой импорт, SQL audit и картографическую публикацию |
| Фильтр «Источники ЭТП» | **Есть в main** в WEB/API и фильтрации по каноническим дублям | Доказать на реальных новых ЭТП и сочетаниях регион/цена/категория/источник |
| P13–P17 | Draft #932 не принят; P15 exact GEO #980 merged; read-only P13/P16/P0 audit #988 **merged** SHA `2f6544b8` | Получить реальные Home SQL/impact артефакты, не перепрыгивать DB backup/restore и полный P13 sale-only gate |
| Кадастровый поиск | **UI-компоненты и API-контракты уже существуют**: suggestions, карточка, границы и fallback-состояния | Проверить источники, точность и все пользовательские сценарии на production |
| Дедупликация | **P0 риск в main:** общий кадастр смешивает продажи; исправление [#987](https://github.com/Maximiliamos/torgi/pull/987) прошло 9/9 CI, но ещё **не merged** | Read-only impact, безопасное принятие в main, затем отдельная контролируемая historical repair |
| Source/region/date integrity | **Draft #958** | Проверка, перенос исправлений, audit/backfill, тесты на реальной БД |
| Map tile cleanup / S3 retention | **Draft #957 / #959** | Валидировать безопасную очистку и контроль immutable-манифестов |
| Внешние сервисы, GitFlic, performance | **Draft #955 / #960** | Протестировать, принять, настроить зеркалирование и мониторинг |
| Документация P12.5 | Полный R0–R11 + P13–P17 + BAT и исправленный README включены в [#989](https://github.com/Maximiliamos/torgi/pull/989), **не merged** | CI + merge #989, проверить runbooks и статус production SHA после релиза |
| Release `v0.3.5` | **Есть ошибка выпуска новой версии под уже занятым тегом** | Новый корректный version/tag, без перезаписи исторической версии |

**Что уже исправлено относительно документа «Что отстаёт»:** `docs/phase3-lite-recovery.md` описывает путь `D:\BankrotAI\dr-backups`, isolated restore, 48-часовую фактическую периодичность и порог 60 часов; в текущем runbook заявлены **три** сохраняемые проверенные генерации, а не одна. `docs/FREE_WEB_DEPLOYMENT.md` помечен как исторический. Cloudflare Tunnel workflow помечен **LEGACY EMERGENCY ONLY**. Issues #836, #853 и #862 уже закрыты. **Нельзя возвращать их в список как вновь открытые без свежих свидетельств.**

**Актуализация 29.10.2026:** ранее указанный пробел manual exact-SHA устранён в [#977](https://github.com/Maximiliamos/torgi/pull/977) (merged, SHA `64acc690`), а некорректная отмена post-deploy smoke исправлена в [#986](https://github.com/Maximiliamos/torgi/pull/986) (merged, SHA `c0d0a040`). Их фактическая end-to-end приёмка на последнем main SHA всё ещё необходима.

## 3. Рабочая модель данных и непреложные правила

### 3.1. Идентичность: объект ≠ торги ≠ публикация

- **Физический объект:** кадастровый номер/совокупность номеров, геометрия, адрес.
- **Процедура продажи:** отдельная продажа, организатор, извещение/номер торгов, период, ценовая траектория и статус.
- **Публикация на источнике:** `(source_system, external_id)` и URL конкретной ЭТП.
- Общий кадастровый номер **не является автоматическим основанием** считать две сделки одной продажей. Повторные торги, продажа доли, аренда и иная процедура должны оставаться отдельными, если нет доказательств эквивалентности.
- В текущем `src/bankrotai/logic.py` найден опасный путь: при общей кадастровой записи `_same_cross_source_lot()` возвращает `True` без самостоятельной проверки идентичности процедуры. Отдельная защита `etp_canary_persistence.py` не устраняет общий риск для всех источников.
- Публикации одной доказанной продажи объединяются в одну карту/карточку с указанием всех площадок; сохраняется provenance, журнал и пользовательское состояние.

### 3.2. Глобальный допуск на публичную карту

Публиковать только запись, для которой **одновременно подтверждены**: недвижимость; именно право собственности/продажа (не аренда, требование, транспорт или неподходящая доля); действующий статус; отсутствие архивирования; корректная каноническая сделка; актуальная подтверждённая source-проекция; достоверная геолокация. Неизвестный статус — не «активен по умолчанию».

### 3.3. Данные и качество

`SourceLot → procedure/sale identity → CanonicalLot/ProcessedLot → history → GEO snapshot → MapDataset → REG.RU S3 → WEB`. Вся запись идемпотентна, а незавершённый обход, HTTP 429, CAPTCHA, смена HTML и ошибка сети никогда не могут инициировать массовое архивирование.

## 4. Программа работ: зависимости, этапы и критерии приёмки

### R0 — Базовая фиксация и аудит перед изменениями (P1)

**Зависимость:** нет. **Цель:** не смешивать старые ошибки и новые релизы.

- Зафиксировать `main SHA`, набор production workflows, текущие deployment SHA Home/REG.RU/Cloudflare, текущий `MapDataset`, S3 manifest, действующую БД и резервные копии.
- Снять **read-only** SQL-профиль `SourceLot`, `ProcessedLot`, `CanonicalLot`, price/status history, GEO, map dataset, количество лотов по источнику/региону/статусу; зафиксировать текущие source pause/circuit состояния и время завершённых полных sync.
- Выгрузить inventory открытых Draft PR и сравнить их с `main`; не сливать старые большие ветки целиком: из #932/#955/#957/#958/#959/#960/#964/#965 переносить отдельные актуальные изменения с независимым CI.
- Создать единую матрицу задач и доказательств (`Task ID`, приоритет, владелец, PR, CI, home smoke, production evidence, DoD, причина блокировки).

**Готово, когда:** есть воспроизводимая baseline-выгрузка/хеши/ссылки, сохранён verified restore и зафиксирована текущая схема; не выполнено ни одной опасной записи в production.

### R1 — P13: чистая публичная карта (P1)

- Разделить и переиспытать код PR #932: единая `is_public_map_eligible`/SQL-policy применяется в MapBuilder, карте, счётчиках, фильтрах, fingerprint, reconciliation и public API.
- Исключить аренду, транспорт/VIN, права требования, движимое, завершённые/неизвестные/архивные записи, ошибочные дубли, ненадёжные координаты и stale source-only точки.
- Для групп дублей отображать актуальную **продажу**, а не физический объект вне контекста конкретного аукциона; канонический root должен содержать истинное состояние и историю всех подтверждённых публикаций.
- Ввести quality-gate с нулевым допуском по категориям `public_rental`, `public_transport`, `public_closed`, `public_movable` и с отдельным мониторингом stale, low GEO quality, locality mismatch.
- Выполнить impact-анализ **перед** публикацией. Исторический audit от 08.10.2026 показывал 45 380 старых точек и лишь 2 741 кандидата после строгой серии фильтров; это **не актуальные октябрьские live-числа**, но важное предупреждение о риске резкого падения покрытия. Нельзя снимать защитные пороги ради формального зелёного релиза.

**Готово, когда:** policy проходит модульные, SQL, API и браузерные тесты; dry-run подтверждает причины всех исключений; новая карта не публикует четыре критические категории; падение количества точек документально объяснено.

### R2 — P14: жизненный цикл источников и исправление дедупликации (P1)

- Нормализовать статусы каждого источника по официальному payload: scheduled, active, closed/finished/cancelled, unknown; особый контракт для публичных предложений, периодов снижения и приёма заявок.
- Для `torgi-russia.ru` устранить предположение «поисковая выдача = active», отдельно проверить pause/circuit и [issue #931](https://github.com/Maximiliamos/torgi/issues/931); не включать источник искусственно для тестовой статистики.
- Для legacy TBankrot: оставить paused/isolated по текущей политике; старые записи не должны самостоятельно удерживать публичную точку, но могут храниться в истории, если подтверждены действующим источником.
- Проверять `SourceLot` freshness, `last_seen_at`, действительные полные циклы, корректные статусы и подтверждение актуальной сделки из действующего источника.
- Пересмотреть `_canonical_key`, `_same_cross_source_lot`, запись `CanonicalLot` и обработку `duplicate_of_id`: общий кадастр → **связь физического объекта**, но не безусловное объединение разных продаж; отдельно распознавать повторные торги/разные доли/право аренды.
- Перенести проверенные исправления временных меток и `region_code` из Draft #958, провести dry-run backfill исторических `first_seen_at > last_seen_at` и неправильных регионов.

**Готово, когда:** regression-пакет демонстрирует отсутствие ложного слияния двух продаж одного кадастра; настоящий дубль между ЭТП объединяется; закрытая или устаревшая проекция не удерживает открытую карту; пользовательские заметки и избранное не теряются.

### R3 — P15: строгий кадастровый GEO и поиск (P1)

- Провайдер NSPD/PKK: принимать геометрию/точку только при подтверждённом **точном нормализованном кадастровом номере** в ответе; запрещены `features[0]` без совпадения, копирование номера запроса в чужую запись, замена точного кадастрового объекта административным centroid.
- Если кадастр существует, но провайдер не подтвердил совпадение, Photon/адрес служит **подсказкой**, не публичной точной координатой; `needs_geo_check=true`, карантин до проверки.
- Сверять регион кадастрового номера, адрес, геометрию, тип объекта, адекватность координат; раздельно хранить кадастровый centroid и адресный fallback.
- Проверить `geo_cadastral_properties.py`: `normalize_nspd_props()` при отсутствии распознанного номера возвращает запрошенный номер для отображения; убедиться, что этот fallback **нигде не считается доказательством совпадения от провайдера**.
- Поддержать polygon/geometry, multi-polygon, здания с отсутствующими границами, кэш и провайдерские ограничения; никаких вымышленных границ.
- **Кадастровый UX из ТЗ:** поиск по номеру → предварительная карточка в левом меню → подробные характеристики → подсветка границ, если доступны; если границ нет — честное «Без координат границ». Адресный запрос → несколько релевантных адресов → выбор → соответствующие кадастровые объекты → полная карточка и торги на карте.
- Контрольные случаи: `76:23:011401:8268` (здание, ожидается граница при подтверждении источником) и `76:23:010101:15008` (адрес «Ленинградский проспект, 105», отсутствие границ допустимо, если источник их не предоставил); отдельный регрессионный пример `76:02:022201:38` — не принимать centroid Борисоглебского района.
- Проверить все заявленные поля карточки: тип, дата учёта, номер и квартал, адрес, наименование, назначение, площадь, статус, собственность, стоимость, этажность, ввод/завершение строительства; `null` показывать как «нет данных», а не придумывать.

**Готово, когда:** два пользовательских сценария из DOCX пройдены в Chromium/mobile, выделение границ соответствует реальной геометрии; неправильные fallback-точки отсутствуют; source provenance виден в диагностике.

### R4 — P16: историческая очистка и перенос данных (P1)

- Dry-run по уже загрязнённой БД: аренда, транспорт/VIN, движимое, closed-but-public, stale TBankrot, source-only stale, invalid category, неверное GEO, ошибочные canonical groups, проблемы дат/регионов.
- Классифицировать **каждую** затронутую запись по причине. Не удалять физически; архивировать/карантинировать с reason, сохранить историю, исходные URL и реестровые ссылки.
- Править только bounded-пакетами с контрольными хешами, журналом, обратимой миграцией и подтверждённой резервной копией; отдельно корректировать неправильно объединённые продажи.
- Переоценить существующие GEO пачками: сначала Ярославская область и 5 контрольных кейсов, потом регионы с максимальной концентрацией weak address fallback/duplicate centroid.
- После ремонта сформировать **кандидат** нового MapDataset; измерить легитимные изменения по регионам, источникам и статусам. Опубликовать только после согласования контрольных показателей и rollback.

**Готово, когда:** аудит до/после доступен в SQL/JSON; для каждого исправления известна причина; обратный переход к сохранённому dataset технически проверен.

### R5 — P17: целостность production и безопасность релиза (P1)

- Исправить `.github/workflows/regru-deploy.yml`: exact Home SHA gate обязателен **для push, workflow_dispatch, rerun, recovery**, если это реальный deploy, а не диагностика; проверка до изменения REG.RU.
- Проверять exact SHA у Home API, REG.RU WEB/API и Cloudflare; `HTTP 200` недостаточен. Включить API version/schema compatibility, migrations, S3 map revision и readiness.
- Публиковать новую immutable карту только после DB→dataset→S3 и policy-gate; проверить `public_*` counts = 0, корректные фильтры, S3 manifest, браузерные tiles, отсутствие незаметной потери регионов.
- Исправить выпуск версий: старый тег `v0.3.5` **не переназначать**; новый patch/minor tag после принятия всех exact-SHA jobs, аттестаций и release evidence.
- Провести pipeline `home → REG.RU → Cloudflare`, smoke на desktop/mobile, rollback endpoint/Dataset, проверку журнала и избранного.

**Готово, когда:** успешен каждый режим запуска deploy и все обязательные production gates именно для выпущенного SHA; реальная карта соответствует данным без закрытых/арендных/транспортных лотов.

### R6 — Платформа-конвейер всех прямых ЭТП (P1)

- Ввести версионированный `EtpSourceContract`: официальный каталог, тип продажи, канонический URL, page/cursor, ID процедуры и отдельного лота, стоимость, график публичного предложения, статус, даты, кадастр/адрес, evidence-поля, запрет неподтверждённого объединения.
- Добавить обязательные стадии `discovered → catalogue_verified → detail_verified → canary_imported → e2e_accepted → regular_sync_enabled`; каждое состояние основано на артефактах, не на названии класса.
- Для каждого источника: ограничители скорости/timeout/max pages; backoff; circuit breakers; HTTP 401/403/429/CAPTCHA — остановка и причина блокировки, **без обхода ограничений**; диагностический бот v6 сохраняет обезличенные свидетельства.
- Реализовать полный catalog traversal с guard от повторяющихся страниц, cursor checkpoints/resume, проверка total/видимых записей, watermark успешной полной выборки.
- Сделать `dry-run → canary (1–2) → bounded sync → full scan → regular schedule`, with `reconcile_missing=False` до двух подтверждённых полных циклов; любые удаления требуют дополнительной независимой проверки источника.
- Настроить состояние площадки, карантин некорректных лотов, измерение качества, идемпотентный retry, отдельный выключатель и rollback на каждую ЭТП.

**Готово, когда:** общий конвейер можно повторно применить к новой площадке без переписывания DB/map/geo бизнес-логики; тесты демонстрируют безопасный отказ при частичном каталоге и 429.

### R7 — Первая полная пара: МЭТС и ЦДТ (P1, критический путь)

**МЭТС:** от нынешних 2 страниц/5 деталей перейти к подтверждённому полному каталогу недвижимости, устойчивому ID, правильным ценам/периодам/статусам; две независимые успешные полные проверки с фиксацией количества лотов и отсутствия дубликатов. Пробный импорт в PostgreSQL после verified restore; затем журнал, GEO, карта и регулярный sync.

**ЦДТ:** проверить PR #975 на живых карточках (нынешняя ошибка `category_unverified`), используемое Nuxt `lotInfo`, tradeId vs tradeLotId, карточки многолотовых процедур, официальный статус, кадастровый номер и стоимость. Если карточки и после исправления невалидны — не включать импорт; документировать источник и причину. После исправления пройти тот же путь, что МЭТС.

**Обязательные тесты обоих источников:** создание → повторный запуск без дублирования → изменение цены → изменение статуса → истечение актуальности → попытка частичного sync/429 → отсутствие незаконного архивирования → PostgreSQL/source + processed + canonical + GEO + journal → S3 → точка на карте → мультифильтр двух источников и их комбинации с Lot-online.

**Готово, когда:** на карте присутствуют подлинные, **обновляемые** лоты обеих площадок, качество подтвердили отдельные выборки исходных карточек; есть production logs со ссылками на exact run/SHA.

### R8 — Подключение оставшихся ЭТП по волнам (P1)

Полная матрица из **22 профилей** приведена в разделе 5. Из них Lot-online уже является штатным источником, остальные новые/дополнительные требуют индивидуальной проверки. Нельзя объявлять «22 работающих парсера» по одному реестру имён.

- **Волна A — табличные и относительно открытые каталоги:** `nistp.ru`, `etpu.ru`, `sistematorg.com`, `centerr.ru`, `aukcioncenter.ru`, `rus-on.ru`, `zakazrf.ru`, `utpl.ru`, `arbbitlot.ru`. У каждой проверить отдельную карточку, статус и границы каталога.
- **Волна B — специфичные карточки / публичные предложения:** `alfalot.ru`, `fabrikant.ru`, `vertrades.ru`, `etp-profit.ru`. Нужны специальные подтверждения статуса и принадлежности недвижимому имуществу.
- **Волна C — сложные API/SPA и множество секций:** `rts-tender.ru`, `sberbank-ast.ru`, `roseltorg.ru`. Изолировать имущественный реестр, собственность, продажу, исключить аренду/арестованные предметы не того типа; доступ без обхода антибота.
- **Волна D — неопределённые публичные каталоги:** `ausib.ru`, `utender.ru`, `electro-torgi.ru`. Сначала установить официальный актуальный listing endpoint/схему; при объективной недоступности — документированный blocker, без ложного «готово».
- Для каждого коннектора — собственная карточка приёмки с двумя подтверждёнными полными циклами, актуальным status/price, URL original lot, coverage, ошибками и end-to-end proof; независимый release switch.

**Готово, когда:** каждая доступная площадка доведена до регулярного production или имеет документированную, проверяемую и повторяемую причину блокировки. Следующий источник подключать можно только при зелёной предыдущей волне без регрессий.

### R9 — Поиск, карта, журнал и пользовательские функции (P1)

- Фильтр `Источники ЭТП` расположен под `Субъекты РФ`, multi-select, union-of-sources; добавить сопоставление canonical sale без потери связи с исходными публикациями.
- Протестировать комбинации `источник + субъект РФ + цена + статус + тип недвижимости + кадастровый номер`; выбор только МЭТС/ЦДТ должен возвращать только их **фактически сохранённые** записи.
- Проверить пустые источники, счётчики, фильтрованные tiles/runtime index, обычные immutable S3 bundles, cache invalidation, desktop/mobile, карту без повторяющихся точек и загрязнения историческими продажами.
- В журнале показывать создание лота, изменения цен/статусов/дедупликации/гео и источник изменений; сохранять заметки, избранное, review и user actions при переобъединении/расклеивании канонических записей.
- Админ-диагностика по source/cursor/error reason/last verified sync с безопасными ссылками на HTML/JSON evidence; не хранить PII в публичных artifact.

**Готово, когда:** ручной E2E-сценарий пользователя и автоматизированные WEB/API/Playwright проверки совпадают с production DB и S3 на одном SHA.

### R10 — P12.5 и технический долг P1/P2 (без подмены функциональных этапов)

- **Документация:** единый действующий `docs/ROADMAP.md`, `README`, `Current Production Architecture`, `Source Registry`, ETP runbooks, cadastre/GEO contracts, P13–P17 release note, матрица известных ограничений. Исторические docs оставить с предупреждением; не стирать историю.
- **Backup:** убедиться, что runbooks/реальный retention в workflow совпадают (текущий recovery runbook говорит о 3 генерациях), протестировать внешнее зашифрованное резервирование, периодическую проверку восстановления и диск/объём. Home/REG.RU не считать независимой DB DR-репликой.
- **Очистка:** изучить PR #957 (PostgreSQL map tile DELETE timeout, ограниченные партии) и PR #959 (safe REG.RU S3 inventory); включать только с полной защитой актуальных/rollback-версий.
- **Георегион и даты:** PR #958 — `first_seen_at`, `last_seen_at`, `region_code`, quality audit; перенести подтверждённые правки и отдельно исправить исторические строки.
- **Иностранные зависимости:** PR #955 — инвентаризация runtime DNS/CDN/maps/AI/library/external calls, сценарии отказа без VPN; выбирать отечественные аналоги, когда они функционально надёжны; не выключать реально используемые Redis/Celery по старой гипотезе.
- **GitFlic и производительность:** PR #960 — резервное зеркало в российском Git, read-only нагрузочные замеры на локальном API, проверка CPU/RAM/SSD/сети домашнего ПК, оптимизация тяжёлых модулей и запросов; подтверждать реальные метрики до/после.
- **Накопленные Draft PR:** #932 (P13–P17), #955, #957, #958, #959, #960, #964, #965. Нужна выборочная инвентаризация, закрытие устаревших веток после переноса, а не единое гигантское слияние.
- **Issues:** #971 ЦДТ и #931 источник Torgi Russia открыты; #969 backup, #836 source reliability, #853 map delivery и #862 WEB smoke закрыты — повторная работа только при новых доказательствах.

**Готово, когда:** все актуальные инструкции соответствуют реальным workflow, зеркало восстановимо, устаревшие PR закрыты с пояснением, рабочие тесты не требуют VPN для критического пути (кроме объективно необходимых сервисов).

### R11 — Общая приёмка и выпуск (P1)

- GitHub: lint, Python/unit, typecheck, SQLite/PostgreSQL integration, миграции, Windows Docker, WEB/Playwright, S3, security, production exact-SHA gates — все критические обязательные jobs для выпуска имеют success.
- Два последовательных подтверждённых **полных** цикла для каждой принятой ЭТП; независимая проверка исходных карточек, цен, даты/статуса, кадастрового номера и связи с source.
- Дедуп: повторный import не создаёт новый лот; другая продажа того же кадастра **не** сливается; одна продажа с разных площадок показывается одной точкой; ручной split безопасен.
- Качество карты: 0 rental, 0 transport, 0 movable, 0 closed/unknown, 0 unsupported rights; 0 недостоверных GEO в выборке, защита от stale-only.
- Проверить пять регрессионных кейсов из «Ошибки.docx» end-to-end и независимую выборку: **50 земельных участков, 30 помещений, 20 домов/квартир**. Каждый проверяется от источника до публичного WEB.
- Проверить cadastre search №/адрес/границы/без границ и фильтры по нескольким ЭТП, журнал, пользовательские действия, S3 dataset, exact SHA, revert/rollback, recovery backup.
- Подготовить **отчёт приёмки по каждой ЭТП** и по каждой из исторических задач: `done`, `blocked` с доказанной причиной, `open` с остатком. 100% нельзя объявлять при открытом критическом пункте или непроверенном публичном результате.

**Готово, когда:** требования пользователя воспроизводимо выполняются на `https://sterdez.online`, дан SHA выпуска, список принятого и исключений, artefacts CI/Home/DB/GEO/Map/S3/browser, подтверждён rollback.

## 5. Полный реестр 22 целевых профилей ЭТП

**Статус столбца** отражает наличие и проверку коннектора на `main` по текущим файлам, а не доступность площадки в будущем. **«Read-only» — не production.**

| # | Источник | Source ID | Текущее состояние | Приоритет и условия перехода в production |
| --- | --- | --- | --- | --- |
| 1 | МЭТС | `m-ets.ru` | Read-only пилот; последний live 5/5 (20 кандидатов), 0 DB writes | **A0:** полный каталог, две сверки, safe canary, статус/цена, карта |
| 2 | Lot-online / РАД | `lot-online.ru` | Действующий источник | **Baseline:** проверить регрессии и фильтр; новый парсер не создавать |
| 3 | Центр дистанционных торгов (ЦДТ) | `cdtrf.ru` | Read-only пилот; последний live 3/5 (11 кандидатов), 2 `cadastral_missing`, 0 DB writes | **A0:** PR #975, lotInfo, ID trade vs lot, live smoke, canary |
| 4 | Новые информационные сервисы (НИС) | `nistp.ru` | Read-only каталог | **A:** detail, статус, полная пагинация |
| 5 | Альфалот | `alfalot.ru` | Read-only каталог | **B:** отдельные разделы статуса/текущих продаж |
| 6 | РТС-тендер | `rts-tender.ru` | Read-only SPA, доступ ограниченно подтверждён | **C:** официальный имущественный grid/API; no anti-bot bypass |
| 7 | Росэлторг / 178-ФЗ | `roseltorg.ru` | Исследование / публичный каталог не доказан | **C:** найти официальный listing contract, тип сделки |
| 8 | Сбербанк-АСТ (имущество) | `sberbank-ast.ru` | Сложный каталог; карточки встречаются в capture | **C:** подтвердить search/export + sale/property scope |
| 9 | Фабрикант | `fabrikant.ru` | Read-only ссылки | **B:** detail, category, pagination/status |
| 10 | АРБбитЛот | `arbbitlot.ru` | Read-only ссылки | **A:** listing route, lot identity, detail |
| 11 | Уральская ЭТП | `etpu.ru` | Read-only таблица | **A:** полная выдача и детализация |
| 12 | Система Торг | `sistematorg.com` | Read-only таблица | **A:** lot subdivision, pagination, status |
| 13 | Центр реализации | `centerr.ru` | Read-only таблица | **A:** detail, filters, sale conditions |
| 14 | Аукцион-центр | `aukcioncenter.ru` | Read-only таблица | **A:** status, full catalogue, details |
| 15 | Профит | `etp-profit.ru` | Профиль; не в 14 пилотных коннекторах | **B:** уточнить две версии сайта, официальный путь |
| 16 | Аукционы Сибири | `ausib.ru` | Исследование | **D:** найти законно доступный актуальный каталог |
| 17 | Вердиктъ | `vertrades.ru` | Read-only; смешанные имущественные секции | **B:** sale vs arrest vs rights, status/identity |
| 18 | uTender | `utender.ru` | Исследование | **D:** каталог/доступ/доказательство стабильности |
| 19 | RUSSIA OnLine | `rus-on.ru` | Read-only таблица | **A:** multi-lot trade, пагинация, статус |
| 20 | Electro-torgi | `electro-torgi.ru` | Исследование | **D:** официальный active listing endpoint |
| 21 | ЗаказРФ (банкротство) | `zakazrf.ru` | Read-only таблица | **A:** detail, цена, статус, каталог |
| 22 | Объединённая торговая площадка | `utpl.ru` | Read-only таблица, доп. профиль | **A:** detail/status/pagination, full sync |

**Отдельно, не путать с прямыми ЭТП:** `torgi.gov.ru` — действующая ГИС; `torgi-russia.ru` и `bidexpert.ru` есть в `default_source_specs`, но их фактический paused/circuit/coverage надо читать из production; `tbankrot.ru` присутствует в коде, однако предусмотрен как изолированный/paused источник; ЕФРСБ целесообразен как источник подтверждения идентичности торгов и статусов только после отдельного контракта и проверки доступа.

**Расширение за пределы 22 профилей:** вести реестр новых ЭТП по тем же admission gates; не считать сайт доступным источником только по наличию домена.

## 6. Пять обязательных регрессионных тестов из ТЗ «Ошибки»

| Контроль | Дефект | Требуемый результат |
| --- | --- | --- |
| `76:23:060521:48` | Аренда помещения, ошибочный GEO, завершённые торги | Не присутствует на карте актуальных **продаж**; исторический след сохранён |
| `LWJPCBL24RB000657` | Мопед/транспорт ошибочно признан недвижимостью | Не присутствует на карте; VIN и текст не обходятся категорийным фильтром |
| `76:22:010717:536` | Аренда земли, ошибочный GEO, закрытые торги | Нет активной точки продажи; статус/геокодирование исправлены/обозначены |
| `76:09:082601:3891` | Завершённые торги | Нет активной метки, корректная история статуса |
| `76:02:022201:38` | Приблизительный центр района вместо границ участка | Нет «точной» точки по адресу без подтверждённого кадастрового matching |

**Дополнительные тесты кадастрового блока:** `76:23:011401:8268` и `76:23:010101:15008`, адрес «Ленинградский проспект, 105» — данные, подсказки, геометрия, карточка, подсветка, отсутствие геометрии без вымысла.

## 7. Стандарт одной ЭТП: чек-лист технической и production-приёмки

| Уровень | Что доказать | Нужный артефакт |
| --- | --- | --- |
| S0 — legal/public access | Официальная публичная страница/API и допустимый режим запросов | Source profile, ограничения, дата проверки, отказ при 401/403/429 |
| S1 — catalogue | Подтверждённая category/sale фильтрация, корректная пагинация и stable IDs | Два каталога, page/cursor evidence, coverage count |
| S2 — detail | Извлечены правильные lot+trade identity, ownership sale, price, status, кадастр | Данные исходной карточки и обезличенный diff |
| S3 — dry-run | Данные проходят нормализацию/дедуп/статусы, но не пишут БД | Dry-run JSON с reason codes |
| S4 — canary | Безопасно создано 1–2 проверенных лота, нет дублей/архивирования | DB counts, журнал, source links, restore gate |
| S5 — E2E | GEO, canonical sale, status/price history, MapDataset/S3/WEB совпадают | SQL + S3 + browser smoke на exact SHA |
| S6 — regular | Два полных успешных цикла, курсоры и обновления работают, источник изолируем | Reconciliation report, failed-page recovery, monitoring |
| S7 — accepted | Внешние ошибки явно обработаны, 0 критических defects, rollback проверен | Signed-off source acceptance record |

Источник не может перейти на `S7`, если `S1/S2/S5/S6` отсутствуют. Исключение с объективным доступом помечается `blocked`, но **не считается «подключённым источником»**.

## 8. Контроль качества, мониторинг и нагрузка

| Метрика | Обязательное правило |
| --- | --- |
| `public_rental_count`, `public_transport_count`, `public_movable_count`, `public_closed_count` | Все **0** перед публикацией карты |
| `unverified_geo_public_count`, `wrong_cadastral_match_count` | Все **0** среди опубликованного с требованием точного кадастрового GEO |
| `source_last_success`, `full_scan_watermark`, `pages_expected/pages_seen` | Реально измеряются по каждому источнику; без полного scan не делать source archiving |
| `new/updated/closed/unchanged/rejected/duplicate` | Фиксировать за каждый цикл и выявлять аномалии; source-specific пороги — после baseline |
| `price_change_events` / `status_change_events` | Сверяются с оригинальной карточкой, не подменяются временем сканирования |
| `geo_needs_review`, `centroid_duplicates`, `region_mismatch` | Отдельные очереди и причинные коды; не маскировать как координаты высокой точности |
| CPU/RAM/disk/network Home | Снять baseline, rate limits, изоляцию workers, лимит одновременных браузеров |
| Release / DB / map / S3 / WEB SHA | Один принятый exact SHA; откат и удержание прошлой версии |
| Backup local / off-host / restore | Не только успешный workflow, но проверенный доступный архив и восстановление |

Нет универсальной фиксированной частоты для всех ЭТП: она определяется официальными ограничениями и нормальной динамикой торгов. Частота и безопасные retry записываются в контракт конкретного источника; ускорение ценой CAPTCHA/rate-limit нарушений запрещено.

## 9. План релизов и критический путь

| Очередь | Пакет работ | Зависимость | Exit gate |
| --- | --- | --- | --- |
| 1 | R0: baseline + DR + инвентаризация PR | — | Evidence зафиксирован |
| 2 | R1/R2: P13/P14, модель продаж и статус | R0 | Карта sale-only, разные сделки не сливаются |
| 3 | R3/R4: P15/P16, кадастровый поиск и historical repair | R1/R2 | GEO fail-closed, данные и история восстановлены |
| 4 | R5: P17 exact-SHA, S3 и release integrity | R0, R1–R4 | Безопасный staged deploy и rollback |
| 5 | R6: общий ETP pipeline | R1/R2/R5 | Повторяемый connector+evidence без DB damage |
| 6 | R7: МЭТС + ЦДТ в production | R6 | Реальные обновляемые точки обеих ЭТП |
| 7 | R8-A/B: табличные и специфичные | R7 | Приёмка каждой площадки по S0–S7 |
| 8 | R8-C/D: сложные и заблокированные | R7 | Production либо документированный blocker |
| 9 | R9: web/journal/filter E2E | R7 и R8 по волнам | User journey+API+DB+S3 совпадают |
| 10 | R10: docs/PR/gitflic/performance | Параллельно с 1–9 | Актуальные runbooks, закрытый tech-debt |
| 11 | R11: полная приёмка | Все принятые источники | Exact-SHA release, source sign-off, 5+100 тестов |

**Ресурсные оценки не являются обещанием сроков:** продолжительность зависит от публичного доступа к ЭТП, количества парсеров, структуры API, возможностей домашнего runner и объёма исторического GEO backlog. Даты целесообразно назначить после R0 и первого принятого E2E коннектора. Работы R1–R5 — безопасность и основание; не заменять их массированным расширением реестра источников.

## 10. Definition of Done — что означает «100%»

**Обязательно одновременно:**

1. Все технически и правомерно доступные целевые ЭТП прошли S0–S7 и содержат реальные обновляемые лоты, либо имеют отдельно проверенный и документированный блокер, исключённый из числа **подключённых** площадок.
2. Полная цепочка `ЭТП → SourceLot → sale/canonical → ProcessedLot → журнал/цены/статусы → GEO → MapDataset → S3 → WEB` проверена и воспроизводится.
3. Чистая карта: нет публичной аренды, транспорта, завершённых и неподтверждённых сделок; нет чужого cadastral feature и нет ошибочно слитых разных продаж.
4. Кадастровый поиск по номеру/адресу соответствует пользовательскому ТЗ и честно отображает реальные границы или их отсутствие.
5. Нет ухудшений существующих ГИС/Lot-online/BidExpert/Torgi Russia сценариев; TBankrot остаётся изолированным по политике.
6. Критический CI, production acceptance и same-SHA Home/REG.RU/Cloudflare/S3 проходят; релиз имеет собственный неизменяемый тег.
7. Backup/restore, dry-run очистки, rollback, monitoring, GitHub docs и operator runbooks проверены на актуальном окружении.
8. Контрольные 5 ошибок и независимая выборка 100 объектов проверены с зафиксированным результатом; издержки нагрузки на ПК приемлемы и измерены.

**Не считается 100%:** зелёный unit CI без реального live-smoke; 22 зарегистрированных имени; read-only API; 2 страницы каталога; 1 успешно распознанный лот; успешный deploy без новых записей на карте; формальное закрытие issues без доказательства.

## 11. Учёт задач / реестр входящих ТЗ

| Исходное требование | ROADMAP IDs | Текущая оценка статуса |
| --- | --- | --- |
| «P13-P17» — sale-only активная карта | R1, R5, R11 | Draft #932, production-приёмки нет |
| «P13-P17» — статусы/canonical/TBankrot | R2, R4, R11 | Есть часть логики, полного доказательства нет |
| «P13-P17» — strict cadastral GEO/NSPD | R3, R4, R11 | Draft #932; backfill/приёмка не завершены |
| «P13-P17» — историческая очистка | R4 | Требуется dry-run и выпуск проверенного dataset |
| «Поиск по кадастровому номеру» | R3, R9, R11 | Код UI есть, фактическая UX/геометрия не принята |
| «Ошибки» — 5 лотов | R1–R4, R11 | Обязательные regression и live production queries |
| «Что отстаёт» — backup docs | R0, R10 | Частично уже исправлено, перепроверить фактический retention |
| «Что отстаёт» — Neon/Tunnel/README | R10 | Исторические пометки появились, README/ROADMAP неполны |
| «Что отстаёт» — ручной exact-SHA gap | R5 | **Исправлен в #977**, merged; отдельный negative manual smoke/rollback всё ещё требует приёмки |
| «Что отстаёт» — issues #836/#853/#862 | R10 | **Уже закрыты**; не открывать без новых свидетельств |
| ТЗ 20+ ЭТП | R6–R9, R11 | Лишь МЭТС/ЦДТ pilot; прочие преимущественно read-only |
| Фильтр ЭТП под «Субъекты РФ» | R9 | В `main` присутствует, нет live acceptance новых ЭТП |
| Бот сбора v6/браузерная диагностика | R6, R8 | Код есть, превратить в поддерживаемый evidence pipeline |
| Дедуп, журнал, GEO, карта | R2, R3, R4, R7, R9 | Общие модули есть; интеграция новых ЭТП не принята |
| Параллельные draft P1/P2 | R2, R4, R10 | #955/#957/#958/#959/#960/#964 остаются открыты |
| Независимость от иностранного, GitFlic, нагрузка | R10 | Draft #955/#960, нет принятого результата |

---

## 12. Следующее конкретное действие разработчика

Начать с **R0**, а в первом рабочем пакете совместить **R2-DEDUP-001** (разные продажи одного кадастра не объединяются), **R5-DEPLOY-001** (same-SHA gate для manual rerun) и **R7-CDTRF-001** (merge/review #975 после live smoke). Параллельно снять read-only impact матрицу для P13–P17. Не выполнять массовый импорт, историческую очистку или замену публичного MapDataset до готовой резервной копии, dry-run и подтверждённого rollback.

**Важно:** этот документ фиксирует задания и проверенный статус. Он не означает, что работы выполнены, что все сайты доступны для парсинга или что существует технически подтверждённый способ собрать любой закрытый источник без нарушения его ограничений.
---

## 13. Обязательное дополнение: объединение с предыдущим GitHub ROADMAP и ТЗ

**Источник:** исторический GitHub [`docs/ROADMAP.md`](https://github.com/Maximiliamos/torgi/blob/f70f128e92aec788a894aeb9ce2ab7cc42f261bd/docs/ROADMAP.md) (базовые этапы Phase 1–5 и все BAT-пункты), а также [полный продуктовый аудит от 05.08.2026](https://github.com/Maximiliamos/torgi/blob/main/docs/audits/2026-08-05-full-product-audit.md), [P14 source status acceptance](p14-source-status-acceptance-20261008.md) и [план восстановления карты P13–P17](p13-p17-verified-data-recovery-plan.md).

**Правило преемственности:** исходные BAT-номера остаются без переименования. Обозначения `R0–R11` являются текущими пакетами реализации, `P13–P17` — отдельной работой по качеству карты, `BAT-001…BAT-312` — историческим продуктовым backlog. Категории P0/P1/P2 в исторических заголовках BAT — старый backlog, а не утверждение о новом срочном P0. При противоречии исторического текста с текущим действующим runtime применяется проверенный новый контракт, а историческое требование отмечается как устаревшее/требующее переоценки; оно не выполняется слепо.

### 13.1. Реестр BAT: что дополнительно внесено из старого roadmap

| Старое ТЗ | Область и незакрытая проверка | Связь с новой очередью |
|---|---|---|
| BAT-001 | SDK коннекторов: перенести остатки логики источников из `scrapers.py`; поддерживать fixture/contract tests для каждой ЭТП | R6, R8, R10 |
| BAT-002 | Разделить physical-object identity, sale/procedure identity и source publication; не объединять две продажи только по кадастру | R1, R2, R6, R11 |
| BAT-003 | Полнота полей торгов: процедуры, расписание цен, задаток, сроки и юридическая специфика | R2, R6–R9 |
| BAT-004 | Версии документов, SHA-256, безопасное получение/хранение/дифф и доступность исходных документов | R9, R10 |
| BAT-005 | ЕФРСБ Publications API: источник подтверждений, реальные условия договора/credentials, законность и доступность | R0, R2, R6, R8 |
| BAT-101 | Калькулятор предельной ставки: проверить полноту web-API, сценарии затрат и регрессионные тесты | R9, R11; отдельная продуктовая приёмка |
| BAT-102 | Контроль участия в торгах: роли, задаток, ЭЦП, документы, заявка, история событий | R9, R11; отдельная продуктовая приёмка |
| BAT-103 | Оценка стоимости только с проверяемыми аналогами, диапазоном и отказом при недостатке данных | После R7–R9; отдельный BAT-трек |
| BAT-201 | Организации/партнёры/tenant scope и распределение ролей | R10; отдельный BAT-трек |
| BAT-202 | Полный outcome dataset и backtesting/recommendation calibration | R10; отдельный BAT-трек |
| BAT-203 | Эксплуатация, метрики, документы, root LICENSE, защищённые backup/restore | R0, R5, R10 |
| BAT-301 | Устаревшее «три источника»: заменить фактическим контрактом active/paused + 4 штатных источника, не активировать TBankrot вслепую; durable jobs/idempotency | R0, R2, R6 |
| BAT-302 | Долгие фоновые AI/GEO задачи, retry/budgets/audit и сохранение статуса после перезагрузки браузера | R3, R9, R10 |
| BAT-303 | Ролевой доступ `reader/analyst/admin`, negative API tests и разграничение доступа к каждому endpoint | R5, R9, R11 |
| BAT-304 | Честное разделение режима read-only и curated actions; соответствующие `403` и UI capabilities | R5, R9, R11 |
| BAT-305 | Безопасный HTML import и потоковый/фоновый Excel export с проверкой типа, размера и сроком хранения | R9, R10; отдельная WEB parity приёмка |
| BAT-306 | Полнота карты: viewport pagination, кадастровая геометрия/WMS, Yandex API key/domain policy | R3, R5, R9, R11 |
| BAT-307 | WEB/desktop parity по всем пользовательским действиям, Playwright contract matrix и negative role tests | R9, R11 |
| BAT-308 | Разделение монолитов `gui.py`, `scrapers.py`, `api.py`, больших WEB-компонентов | R6, R10 |
| BAT-309 | SLO, наблюдаемость, latency/queue lag/source freshness, защищённые off-host backup и rollback drill | R0, R5, R10 |
| BAT-310 | SBOM, подписанные релизы, неизменяемые теги, required checks/branch protection и secret scanning | R5, R10, R11 |
| BAT-311 | UI-модули, i18n, design system, keyboard/focus/contrast/WCAG 2.2 AA | R9, R10 |
| BAT-312 | Доказательная оценка активов, сделки, фактическая прибыль и калибровка рекомендаций | После R9; отдельная продуктовая приёмка |

**Статус:** пункты выше являются требованиями, **а не списком подтверждённых дефектов**. Старый ROADMAP помечал ряд foundations как delivered, но для BAT-101–312 нет достаточных свежих end-to-end свидетельств, чтобы объявить их закрытыми сегодня. Выполнение любой задачи подтверждается отдельным `main SHA`/тестом/production evidence. Старые аудитные заметки не доказывают, что дефект существует в текущем релизе.

### 13.2. Из аудитов GitHub — дополнительная проверка открытых требований

- **Безопасность и доступ:** повторно проверить владельцев/роли, permission checks, tenant isolation, administrative GEO/backup/recovery, curated POST и отрицательные тесты, а не опираться только на исторический ревью.
- **WEB/desktop:** подтвердить маршруты импорта HTML, Excel, batch AI/GEO, документов, контроль участия, калькулятор, историю, избранное и rollback; отметить `delivered` лишь по современной acceptance matrix.
- **Документы и права:** версии и метаданные не гарантируют проверенного download worker, content comparison, antivirus/size validation, ACL и безопасного object storage.
- **Полнота рынка:** проверить фактическую активность четырёх основных источников через production `paused/circuit`, отдельно ЕФРСБ и ограничения договора/credentials. Наличие `default_source_specs()` не равно свежему полному обходу.
- **Кадастр и метаданные:** отображаемый кадастровый номер, взятый из пользовательского запроса, не является доказательством ответа провайдера. Не использовать его для одобрения геометрии или реестровой идентичности.
- **Восстановление:** на `main` backup workflow использует `D:\BankrotAI\dr-backups`, `-RetainCount 3`, isolated restore и порог 60 часов. Не переносить старое замечание о `RetainCount=1` на актуальный workflow.
- **Доказательства качества:** строгая политика P13–P17 по старому аудиту резко уменьшает число допускаемых к карте записей. Не обходить quality gate изменением `MIN_MAP_COVERAGE_RATIO`; сначала восстановить действительно свежие статусы и точную геометрию, затем пересмотреть baseline согласно источникам.

### 13.3. ПЕРВАЯ ОЧЕРЕДЬ — конкретные работы для разработчика

Первые изменения оформлять небольшими независимыми PR, не выполнять один giant-merge старых Draft-веток.

| Порядок | Задача/ID | Действие | Что считается доказательством выполнения |
|---|---|---|---|
| **1.1** | **R0-BASELINE-001** | Снять exact SHA Home/REG.RU/Cloudflare, зафиксировать live `MapDataset`, активные и paused источники, 5 контрольных кейсов, off-host DR статус; прочитать фактический source freshness/coverage | Read-only `baseline.json`, link на workflows, без DB writes |
| **1.2** | **R0-IMPACT-002** | Запустить существующий `scripts/p13-production-eligibility-impact.py`, до/после по состояниям `rental/closed/transport`, GEO и stale-source с сохранением количества валидных объектов | SQL/JSON impact matrix; review на качественно допустимую карту |
| **1.3** | **R1-ELIGIBILITY-001 / R2-DEDUP-001** | Разбить PR #932: общий sale-only/real-estate/active map gate и независимое исправление алгоритма cross-source merge; несколько продаж одного объекта **не** сливать | Unit+PostgreSQL regression; 5 owner cases; 0 опасных новых точек |
| **1.4** | **R5-DEPLOY-001** | Исправить exact-SHA зависимость REG.RU от Home для `workflow_dispatch`, rerun и recovery, не только push; stop до SSH/deploy | Green тесты четырёх режимов запуска; одинаковый SHA |
| **1.5** | **R7-CDTRF-001** | Проверить PR #975 после тестов на живых карточках: `lotInfo`, `category_label`, `tradeId`/`tradeLotId`; сделать повторный home smoke | `cdtrf.ru`: не менее одной доказанно корректной карточки и 0 слабых верификаций |
| **1.6** | **R3-CADASTRE-001 / R4-REPAIR-001** | Ограниченный GEO dry-run по пяти ошибкам + два сценария адресного/кадастрового поиска; реальная сверка по провайдеру, quarantine загрязнённых данных | Протокол original evidence, exact matching, отсутствие фальшивых границ и ложных центроидов |
| **1.7** | **R6-ETP-CONTRACT-001 / R7-METS-001** | После безопасных R1/R2/R5 и подтверждённого backup запустить канареечный импорт МЭТС, затем ЦДТ, проверить журнал/geo/map/S3 в одном SHA | Два `S5` E2E на реальных лотах; без массового архивирования |

**Порог первой очереди:** не начинать пакет R8 массовых источников, пока не прошли `R2-DEDUP-001`, `R5-DEPLOY-001`, карта не прошла P13 impact review, и не принят первый источник уровня `S5`.

### 13.4. Приоритеты, которые не должны задерживать первую пару ЭТП

`BAT-103/201/202/312` — инвестиционная оценка, партнёры и outcome learning — остаются отдельным **не закрытым по умолчанию** продуктовым направлением и выполняются после критического потока R0–R9; `BAT-303/304/310` при этом проходят минимум необходимой безопасности до включения source-admin действий. Владелец может назначить им собственные приоритеты без влияния на чистоту карты.

### 13.5. Проверка и изменения в GitHub

- При исполнении плана вести отдельные Issues/PR на каждый пункт `R0-...`, `R1-...`, `BAT-...`, с указанием связанного исходного ТЗ, acceptance criteria, CI и живого `main SHA`.
- Настоящий документ фиксирует актуальные цели. Для осведомлённости старые phase 1–5 считались принятыми **в историческом объёме**; они не закрывают текущие требования P13–P17, прямые ЭТП и новый кадастровый поиск.
- Не переносить рабочие ветки #932/#965 целиком: они устарели относительно `main` и содержат взаимосвязанные изменения. Переносить изолированные подтверждённые файлы и исправления, предварительно перепроверяя конфликтующие алгоритмы.
- Не включать новые производственные источники, не писать в БД и не переносить метки карты в ходе чисто документационного изменения ROADMAP.

## 14. Полный исторический BAT-backlog из прежнего `docs/ROADMAP.md` (сохранён без удаления требований)

**Ниже сохранён текст исходных требований GitHub, доступный до объединения 29.10.2026.** Отдельные датированные утверждения в этих разделах являются историческим статусом и должны перепроверяться относительно текущего `main` и production. Этот appendix нужен для полноты и трассируемости, он не создаёт новый P0 по умолчанию.

### P0 — надёжное ядро торгов

#### BAT-001 — Connector SDK and registry — foundation delivered

- единый async-контракт `search/fetch_lot/fetch_documents/fetch_events/healthcheck`;
- capability declaration, cursor и metadata;
- адаптеры ГИС «Торги» и TBankrot без дублирования существующей логики;
- fixture/contract tests для каждого нового источника.

Done: SDK, registry and first adapters exist. Remaining: move source-specific implementation out of `scrapers.py` incrementally.

#### BAT-002 — Canonical and source identity — foundation delivered

- `CanonicalLot -> SourceLot[]`;
- уникальность `(source_system, external_id)`;
- консервативное объединение по кадастровому номеру, ЕФРСБ или делу+процедуре;
- ручное merge/split с audit log до автоматического fuzzy matching.

Done: schema, migration and ingest synchronization. Remaining: reviewed merge/split UI and stronger identity evidence.

#### BAT-003 — Auction procedure fields — foundation delivered

- ЭТП, процедура, извещение, сообщение ЕФРСБ;
- должник, организатор, арбитражный управляющий, дело;
- задаток, сроки, аукцион, шаг, публичное предложение;
- фильтры и deadline notifications.

Done: normalized indexed storage and detail API. Remaining: filters and notification jobs.

#### BAT-004 — Immutable document history — foundation delivered

- document identity per source lot;
- SHA-256 versions and storage keys;
- comparison pipeline for changed requisites, deadlines, asset composition and contract terms;
- malware scanning and S3/MinIO lifecycle policy before production uploads.

Done: schema and idempotent version recorder. Remaining: download workers, object storage and semantic diff.

#### BAT-005 — Official EFRSB Publications API — integration-ready

- operator-issued credentials only;
- `/v1/auth`, trade message search/detail and authorized archive URL;
- rate limiting, retries, fixtures and production contract monitoring;
- no CAPTCHA bypass or browser scraping.

Done: authenticated connector contract. Blocked for live acceptance: production service agreement and credentials.

### P1 — investment decision and participation

#### BAT-101 — Maximum bid calculator — delivered API foundation

- explicit conservative sale price;
- repair, legal, holding, tax, commission, capital, target profit and risk reserve;
- pessimistic/base/optimistic scenarios;
- maximum bid, profit, ROI, annualized return and break-even.

Done: desktop/web form and saved assumptions. Remaining: approval history.

#### BAT-102 — Participation control — delivered data/API foundation

- ETP accreditation and signature validity;
- application, deposit purpose/sending/receipt, signed documents and acceptance;
- countdowns from normalized procedure deadlines;
- discrepancy warnings between notice and documents.

Done: desktop/web checklist UI. Remaining: notifications and document-to-field reconciliation.

#### BAT-103 — Evidence-based valuation

- comparable dataset with distance, price/m², date, exposure and condition;
- separate liquidity, expense and risk models;
- LLM only explains precomputed values;
- no price output when evidence policy is not met.

### P2 — Deal Room and learning loop

#### BAT-201 — Organizations, roles and partners

- tenant-scoped users and RBAC;
- ownership, assignments, approvals and audit trail;
- planned/actual partner contributions and profit allocation.

#### BAT-202 — Outcome dataset

- recommendation -> bid -> purchase -> expenses -> sale -> actual profit;
- immutable model/prompt/data provenance;
- backtesting, calibration and drift monitoring.

#### BAT-203 — Operations

- PostgreSQL primary production database;
- S3/MinIO documents, encrypted backups and restore drills;
- connector health dashboard, metrics, alerts and runbooks;
- owner decision and publication of a root `LICENSE`.

### P0 — завершение безопасного web/desktop parity

#### BAT-301 — Three-source bulk synchronization

- единая Celery orchestration для ГИС «Торги», TBankrot и РАД/ЛОТ-ОНЛАЙН;
- idempotency key, per-source cursor/progress/retry/cancel и частичный результат;
- web показывает недоступные источники и не объявляет неполный проход успешным;
- acceptance: повторный запуск не создаёт дублей, progress восстанавливается после restart worker.

#### BAT-302 — Background AI and GEO operations

- queue endpoints для single/batch AI, GEO, retry failed GEO и re-geocode;
- server-side concurrency/rate limits, budget limit и audit actor;
- acceptance: browser disconnect не останавливает job, статус доступен после повторного входа.

#### BAT-303 — Real RBAC and tenant isolation

- `reader` только читает, `analyst` меняет личный workflow, `admin` запускает jobs и merge/split;
- tenant/user scope для watchlist, notes, scenarios, checklist, audit log;
- отрицательные API-тесты для каждого endpoint и роли.

#### BAT-304 — Honest deployment modes

- заменить двусмысленный `API_READ_ONLY` на curated/read-only режимы;
- отдельные allowlists методов и route capabilities endpoint для UI;
- скрывать недоступные кнопки, возвращать `403` для запрещённого действия.

### P1 — оставшиеся desktop-возможности в web

#### BAT-305 — Import/export jobs

- безопасная загрузка HTML с size/type limits и malware scan;
- Excel export как streaming/background download с теми же колонками и фильтрами;
- retention и удаление временных файлов по политике.

#### BAT-306 — Map parity

- Yandex provider как optional web layer без silent fallback после успешной загрузки;
- кадастровый WMS/proxy, marker clustering и серверная фильтрация цены/status/review;
- viewport pagination вместо фиксированного лимита 5000 точек.

Done 2026-08-05: web переведён с Leaflet на Яндекс.Карты, перенесены кластеризация,
кадастровая геометрия, desktop-маркеры и смена статуса «светофором». Remaining:
серверная viewport pagination и production API key/domain policy Яндекс.Карт.

Follow-up 2026-08-05: web повторяет desktop-композицию карты в двух состояниях —
панель кадастрового поиска/фильтров и карточка выбранного лота с фото, ссылками,
процедурой и оценкой. Map feature вынесен из `main.tsx`; `allow-same-origin`
удалён, сообщения защищены проверкой окна, opaque origin и уникального channel id.

Follow-up 2026-08-05: устранены два дефекта внешнего ревью. Совпадающие координаты
теперь показываются счётчиком и раскрываемым списком лотов; серверные группы дублей
передают полный список исходных публикаций. Iframe и экземпляр Яндекс.Карт стали
долгоживущими, поэтому открытие карточки и изменение оценки сохраняют viewport.
Ручное объединение/разделение групп защищено ролью `admin`. Это покрыто API- и
Playwright-регрессиями; remaining BAT-306 не изменился: viewport pagination и
production API key/domain policy.

#### BAT-307 — Web parity acceptance suite

- Playwright journeys: search/import, registry/detail, watchlist, map/review,
  calculator/history, participation, documents diff и duplicate review;
- contract matrix «desktop action → API → web screen → role → automated test»;
- parity считается готовым только при зелёной матрице, а не по наличию кнопки.

Done 2026-08-05: базовый Playwright journey проверяет вход, реестр, поиск,
ручной/списочный регион, фиксированную категорию, Яндекс.Карту, сделку и диагностику.
Карта дополнительно проверяется на трёх лотах с одинаковыми координатами,
индивидуальный выбор, неизменность `contentWindow`, центра и масштаба после выбора
и изменения «светофора».
Remaining: сценарии импорта и изменения данных для каждого workflow и роли.

### P1 — аудит и эксплуатация

#### BAT-308 — Split monoliths

- разнести GUI по feature widgets/controllers, API по routers/services, scraper implementations по connectors;
- не допускать новых модулей свыше 1000 строк без ADR.

#### BAT-309 — Observability and recovery

- SLO, Prometheus/OpenTelemetry, queue lag/source freshness/GEO/AI error alerts;
- PostgreSQL encrypted backups, quarterly restore drill и документированный RPO/RTO;
- release rollback rehearsal и migration compatibility window.

#### BAT-310 — Supply chain and governance

- root `LICENSE`, SBOM, signed release images, dependency update policy;
- branch protection, required checks, secret scanning/private vulnerability reporting.

### P2 — качество интерфейса и данных

#### BAT-311 — Frontend architecture and accessibility

- feature modules/routes, schema-derived labels, i18n и единый design system;
- WCAG 2.2 AA keyboard/focus/contrast tests.

#### BAT-312 — Outcome and valuation evidence

- comparable evidence policy и отказ от оценки при недостаточных данных;
- полный outcome dataset, backtesting, calibration и drift monitoring.

### 2026-11-07 — seven-source production recovery (P0 active)

- Analyzed GitHub run `42336240166`, DB run
  `3fd5f3bf-fbf5-4463-b402-86c8a40d5243`, delayed 09:54 MSK scheduled run
  `79b84f12-b68b-49fb-a600-79de6438e027`, and M-ETS retry
  `097bf5f9-9838-4bc6-a38b-5c660fdbde29`.
- Confirmed three independent blockers: unbounded wide `source_lots` expiry
  SELECT, fail-closed Torgi Russia record cardinality at region 36/page 2, and
  strict equality of a live M-ETS reported total during a multi-hour walk.
- Implemented locally: bounded PK expiry scan; bounded M-ETS churn tolerance
  with paginator terminal proof, overlap/large-drift refusal and no missing-lot
  reconciliation; canonical numeric-string Torgi Russia IDs and safe rejection
  reason evidence.
- Full local suite: 1,259 passed, 12 skipped; the sole Windows failure was a
  pre-existing test comparing UTC lease timestamps to local Moscow wall time
  and is corrected to use `utc_now()` on this recovery branch.
- Map publication remains blocked: repeated complete local builds are rejected
  by REG.RU S3 with `403 QuotaExceeded`. A fail-closed read-only planner now
  inventories every object and every retained manifest, preserves all
  referenced/recent objects, and proposes only unreferenced immutable objects.
- Remaining order: obtain the sanitized no-VPN shape of the one Torgi Russia
  record; run CI and read-only S3 plan; independently review and apply an exact
  orphan set; merge/deploy exact SHA; backup/restore preflight; rerun full seven
  sources; finish GEO; publish and verify S3 manifest + sterdez.online map;
  then observe two independent 09:00/21:00 scheduled cycles.


## 2026-11-09 — Home read-only audit executed

Operator executed `scripts/run-readonly-postrun-audit-home.ps1` against the running Home worker image:

- running image: `bankrotai-home-api:66a91d8ef2a73ec83b80c70363b8406d6b2e1d64`;
- report: `D:\\BankrotAI\\etp-operator-logs\\audit-20261108-20261109-021857.json`;
- run status: `partial`;
- current map: `20261108T215500614188Z-r6-bundle-s3`;
- public REG.RU S3 manifest: **verified**;
- source-counter archive total: **1,383**;
- run-window archive rows exactly matched the source counters:
  - BidExpert: **1,332 / 1,332**;
  - GIS: **49 / 49**;
  - Lot-online: **2 / 2**;
  - all other selected sources: **0 / 0**.

This closes the previous uncertainty that the aggregate archive counters might not correspond to rows archived in the run time window. It does **not** by itself prove that every archive was semantically correct; reason-code/sample review remains the provenance gate before any source-scope narrowing, especially BidExpert `all -> realty`.

The audit also independently confirms that the published public S3 manifest exists for the current MapDataset. Run-specific GEO attribution for all 87 inserted source rows still remains a separate evidence question unless the JSON report contains the per-row GEO counts/samples.

Release gate update:
- CODE: YES
- TEST: re-run required on final integration head after importing remaining safe #1160 changes
- HOME READ-ONLY AUDIT: YES
- PUBLIC S3 MANIFEST: YES
- ARCHIVE WINDOW COUNTERS: YES
- ARCHIVE SEMANTIC CORRECTNESS: NOT FULLY PROVEN
- MERGED: NO
- DEPLOYED: NO
