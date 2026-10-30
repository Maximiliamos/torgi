# TORGI / Sterdez.online — аудит парсеров и внешних аналогов (30.10.2026)

**Основание:** inspected files in `main` and `feat/cdtrf-catalog-timeline-20261030`, supplied CDTRF catalogue HTML `21.html`, public GitHub source code listed below. **This is a code audit, not a live production verification.**

## Наши источники

| Группа | Источники | Подтверждение | Не доказано |
| --- | --- | --- | --- |
| Штатные | `torgi.gov.ru`, `lot-online.ru`, `tbankrot.ru`, `torgi-russia.ru`, `bidexpert.ru` | В `default_source_specs`, адаптеры в `connectors/registry` | Свежесть/полный охват каждого в live |
| Отдельный API | `fedresurs.ru` | Коннектор к официальному Publications API | Production auth / полная интеграция |
| Детальные пилоты | `m-ets.ru`, `cdtrf.ru` | Detail parsers, verified ID, bounded windows | S5–S7 production acceptance |
| Прямые read-only | `nistp.ru`, `alfalot.ru`, `rts-tender.ru`, `fabrikant.ru`, `arbbitlot.ru`, `etpu.ru`, `sistematorg.com`, `centerr.ru`, `aukcioncenter.ru`, `vertrades.ru`, `rus-on.ru`, `zakazrf.ru`, `utpl.ru` | `direct_etp_pilots.PILOT_SOURCE_IDS` и `direct_etp_catalogue.PROFILES` | Проверенные реальные детали и регулярная загрузка |
| Исследовательские | `roseltorg.ru`, `sberbank-ast.ru`, `etp-profit.ru`, `ausib.ru`, `utender.ru`, `electro-torgi.ru` | Профили и частичные вспомогательные capture | Полный работоспособный коннектор |

**Общие проблемы:** неодинаковая полнота `NormalizedLot`, цены через `float`, разная полнота статусов, нет полностью принятого контракта часов, риск ошибочной дедупликации продаж по одному кадастру, часто read-only preview ошибочно принимают за production.

## CDTRF: техническая реализация PR #1022

- Сохранённая страница 30.10.2026 показывает `20/50/100` в переключателе; `1000` не подтверждён официальным API.
- `etp_cdtrf_full_catalogue.py` делает полный **read-only** проход выбранного каталога с `totalCount`, `PageSize` и проверки количества и ID.
- `cdtrf_sync.py` — отдельный адаптер: каждый каталог-пейдж 20/50/100, затем все property-hint details на странице с проверкой `tradeId` и `tradeLotId`; сохранение через основной `NationwideIngestionService`.
- `services/ingestion.source_full_specs('cdtrf.ru')` разрешается только при трёх операторских флагах, including verified backup. **Нет добавления в default scheduling** и нет missing-lot archival.
- Проверка статусов пяти стадий, даты заявок и их timezone, type, schedule. Текущая цена по недоказанному периоду остаётся `null`.
- Код не доказывает стабильный snapshot API, отсутствие защит у оператора или готовность GEO/map/live sync. До production: exact SHA/CI, live read-only scan, backup+restore, canary DB, полный E2E и две стабильные синхронизации.

## Публичные GitHub-аналоги (реально просмотренные файлы)

1. [nkleym/bankrot-bot](https://github.com/nkleym/bankrot-bot/tree/main/bankrot_bot/parsers): `mets_selenium.py` и `sberbank_selenium.py` — Selenium, `max_pages=3` по умолчанию; полезна идея `BaseParser` / `ParsedLot`, но ограниченный трёхстраничный обход нельзя считать полнотой. Список файлов и код подтверждены.
2. [gorbanevv/torgi-sentinel](https://github.com/gorbanevv/torgi-sentinel/tree/main/src): `torgiClient.js` — `page/size`; `poller.js` разделяет seed, catch-up, флаг truncated; `rateLimiter.js` ограничивает нагрузку. Применимо для разделения historical/full и incremental, но не доказывает надёжность нашей карты.
3. [igorvolk1961/zakupki_parser](https://github.com/igorvolk1961/zakupki_parser/blob/main/docs/platforms.md): профили `enabled`, параметры пагинации, статусов, селекторы, источник API/SPA. Полезен паспорт ЭТП; специализация — закупки, не всегда продажи недвижимого имущества.
4. [FedorSmorodskii/Fedresurs_Arbitr_Parser](https://github.com/FedorSmorodskii/Fedresurs_Arbitr_Parser/blob/main/src/settings.py): Scrapy, concurrency, AutoThrottle, retry codes 429/5xx и собственный backoff.
5. [SergeyAbdorin/Parsing_torgi.gov.ru](https://github.com/SergeyAbdorin/Parsing_torgi.gov.ru): XML → детализация извещений → кадастровые номера/VIN → Google Sheets; полезный пример разделения discovery/detail, но без нашей модели provenance, journal, GIS.

## Стандарт для следующей ЭТП

Определить **официальный** catalogue+detail contract → получить стабильный lot ID → пройти все страницы и reject truncation → проверить detail → преобразовать в `NormalizedLot` → safe database import → журнал/geo/map → S0–S7 acceptance. Любые 401/403/429 или CAPTCHA означают ограничение доступа, не повод обходить защиту.

Полный документ для разработки: **«ТЗ: унифицированные парсеры ЭТП для TORGI / Sterdez.online», версия 1.0, 30.10.2026, DOCX** (передаётся отдельно). Утверждение схемы отслеживается в [#1021](https://github.com/Maximiliamos/torgi/issues/1021).
