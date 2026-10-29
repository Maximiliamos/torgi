# TORGI — проверенный read-only production-аудит (29.10.2026)

**Область:** P0/BAT-002, P13–P17, R0–R8, ЭТП. **Исходный Home/WEB SHA:** `3171245b72931f547c9d816f4a0e44abc697f867`. **Режим:** только чтение; этот документ не разрешает SQL UPDATE, массовый импорт, публикацию карты или смену статусов.

## 1. Доказательства работоспособности предыдущего production SHA

- [Home deploy](https://github.com/Maximiliamos/torgi/actions/runs/40918192981), [REG.RU deploy](https://github.com/Maximiliamos/torgi/actions/runs/40918193154), [Cloudflare deploy](https://github.com/Maximiliamos/torgi/actions/runs/40918192963): success.
- [P11 production acceptance](https://github.com/Maximiliamos/torgi/actions/runs/40918192944), [Public WEB smoke](https://github.com/Maximiliamos/torgi/actions/runs/40926715246), [Production functional reliability](https://github.com/Maximiliamos/torgi/actions/runs/40926715321): success.
- Приведённые результаты относятся **только** к `3171245b`; они не подтверждают более поздние merge SHA.

## 2. P0: исторические связи разных продаж

Источник: [P16 read-only Home integrity baseline](https://github.com/Maximiliamos/torgi/actions/runs/40925467727), артефакт `p0-historical-dedup-impact` (29.10.2026).

| Показатель | Значение | Толкование |
| --- | ---: | --- |
| `cross_source_merged_pairs` | 48 724 | Подозрительные пары; не 48 724 доказанные ошибки |
| `canonical_groups_multiple_sources` | 26 561 | Канонические группы с публикациями нескольких источников, отдельно от пар |
| `same_cadastre_not_sale_identity` | 40 777 | Общий кадастр не доказывает идентичность сделки |
| `conflicting_cadastres` | 7 430 | Разные кадастровые номера внутри объединённой пары; высокий приоритет ручной проверки |
| `missing_cadastral_evidence` | 517 | В паре отсутствует достаточное кадастровое свидетельство |
| `region_conflict` | 140 | Несовпадение региона внутри пары |
| `source_status_divergence` | 20 673 | Статусы расходятся; могут отражать разные моменты обновления |

Категории причин **пересекаются**, их нельзя суммировать. Само присутствие в отчёте не доказывает неверное объединение.

- [#993](https://github.com/Maximiliamos/torgi/pull/993) merged как `a7586937`: устранены ошибки новых timestamp/region записей; старые строки не переписаны.
- [#994](https://github.com/Maximiliamos/torgi/pull/994) merged как `e527fedf`: консервативная идентичность новой продажи и сохранение старых канонических связей при обновлении; 9/9 CI. Это **не** историческая очистка. P11 на новом SHA должен быть подтверждён отдельно.

**Порядок исторического ремонта:** извлечь только ограниченные кандидаты и первоисточники; классифицировать «тот же лот/другая продажа/неизвестно»; сделать verified-restore backup, обратимый SQL dry-run и критерии сохранности notes/watchlist/price history; затем исправлять небольшими пачками и сравнить old/new canonical. Не объединять/разбивать автоматически по одному кадастру или цене.

## 3. P13: вероятность резкого уменьшения покрытия карты

Источник: [P16/Home baseline](https://github.com/Maximiliamos/torgi/actions/runs/40925467727), артефакт `p13-live-public-map-impact` — read-only расчёт к 29.10.2026.

| Метрика | Значение |
| --- | ---: |
| Точек в опубликованном `MapDataset` | 51 705 |
| `db_unarchived_mapped_primary` | 52 956 |
| Максимум кандидатов после предложенного P13 policy | **639** |
| Отношение 639 к текущему количеству точек | **1,24%** |
| `preview_fails_existing_coverage_guard` | **true** |

Последовательные (неперекрывающиеся **на этом этапе**) исключения:

1. Активный статус: минус **30 140**.
2. Недвижимость, отсутствие VIN: минус **143**.
3. Продажа, не аренда/транспорт по дополнительным признакам: минус **0** после предыдущего шага.
4. Строгое cadastral GEO: минус **14 813**.
5. Независимый свежий действующий источник: минус **7 221**.
6. Итог: 639. Это **верхняя оценка кандидатов**, а не готовая карта.

Дополнительная диагностика: 30 123 `expired` primary лотов; по source lifecycle у этой когорты причина `missing_after_two_complete_syncs`; их нельзя автоматически переименовать в `active`. Из геоданных до строгого GEO-шага 15 780 имеют `photon`, 6 843 — `nspd`; `geo_cadastral_untrusted_source` = 14 015. Для 5 контрольных owner кейсов в предварительной политике допустимых точек — 0 (мопед: запись в этом наборе не найдена).

**Нельзя публиковать такой P13 dataset вслепую.** Требуется: перепроверить у первоисточников статус/полноту двух успешных обходов, отличать `expired` от подтверждённого `closed`, исправить кадастровые GEO evidence, проверить текущие группы дублей и объяснить разницу по регионам/источникам. Публикация возможна только после обоснованного impact review и rollback; нельзя снижать quality gate только для формального числа точек.

## 4. Прямые ЭТП: что подтверждено

- [22-source readiness](https://github.com/Maximiliamos/torgi/actions/runs/40925467730): все **22/22** профиля обследованы read-only, **0** сертифицировано для production ingestion только по этому обследованию.
- [МЭТС: пагинация](https://github.com/Maximiliamos/torgi/actions/runs/40925467762): 5 просмотренных страниц, **100** уникальных идентификаторов каталога.
- [ЦДТ: пагинация](https://github.com/Maximiliamos/torgi/actions/runs/40925467763): 5 просмотренных страниц, **100** различных идентификаторов, 46 candidate hints.
- [Home limited live detail](https://github.com/Maximiliamos/torgi/actions/runs/40925467810): МЭТС — 20 кандидатов / 5 проверенных карточек; ЦДТ — 10 кандидатов / 2 проверенных / 3 отклонённых (`unsupported_property_right` 1, `lot_identity_mismatch` 2). Без записей в БД.
- [ETP DB post-import audit](https://github.com/Maximiliamos/torgi/actions/runs/40925467727): **0** `SourceLot`, **0** `ProcessedLot` для МЭТС и ЦДТ на момент проверки. Журнал/GEO/карта не доказаны.

Проверки пяти страниц не подтверждают полноту каталога, честный lifecycle, регулярный sync, цены и статусы. Стадии S5/S6/S7 обоих источников остаются открыты. Не использовать `reconcile_missing=True` при частичном обходе.

## 5. Незавершённые приёмочные ворота

1. **P0:** exact-SHA Home P11 после #993/#994; подтвердить актуальные SQL impact и стабильность четырёх действующих потоков.
2. **P13–P16:** восстановить доказанные связи source, уменьшить GEO/status false negatives без публикации вымышленных лотов; пять owner кейсов + независимая выборка 100.
3. **R7:** отдельный операторский canary импорт не более двух валидных лотов с каждой ЭТП через `.github/workflows/etp-manual-verified-import.yml`, с успешным restore-verified backup, SQL audit, GEO, журналом и картой; затем два полных цикла и ограниченный регулярный sync.
4. **R8:** источники по отдельности с честными blocked/accepted статусами, без выдуманных данных.
5. **R11:** #996 merged `49510a7e` (9/9 CI). Остаются P11/Home/REG.RU/Cloudflare/Public WEB/functional proof на **точном выпускаемом SHA**, immutable GitHub Release v0.3.6 и проверка SBOM/SHA256SUMS. Старый `v0.3.5` закреплён за другим SHA и не переназначается.

**Смысл документа:** фиксировать реально подтверждённые цифры и невозможность считать диагностические S0–S2 источники внедрёнными; он не является производственным разрешением на запись в PostgreSQL.
