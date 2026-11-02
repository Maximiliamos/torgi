# TORGI / Sterdez — проверка исполнения шести направлений, 02.11.2026

**Характер документа:** подтверждённый read-only аудит GitHub/P1 и учёт предлагаемых PR. Сам по себе этот документ и открытые PR **не являются** production acceptance, не изменяют PostgreSQL, GEO и S3.

## Источники доказательств

- Текущий при проверке `main`: `e4cbf912bb8dbf32331e8a35d938d9f2234a4541`.
- Последний прошедший production P1: [#41366827021](https://github.com/Maximiliamos/torgi/actions/runs/41366827021), проверка `2026-11-02T00:30:21Z`; `healthy=true`, **8 warnings**.
- [CDTRF import #41361784775](https://github.com/Maximiliamos/torgi/actions/runs/41361784775): 33/34 страниц, 909 новых, 498 обновлено, затем `detail acceptance collapsed`. Проверка импорт→GEO→S3→WEB на новом SHA не подтверждена.
- [Общий импорт #41354717203](https://github.com/Maximiliamos/torgi/actions/runs/41354717203): `partial` при загрузке МЭТС (155 страниц) и Sistematorg (**ровно 500 страниц — предел `max_batches`**, окончание каталога не доказано), затем конфликт GEO-lock; нет полного успешного набора всех источников. Из 16 source-run 14 упали на сетевых тайм-аутах до первой страницы; два статуса `success` не доказывают полный source inventory.
- Production P11 для `a56203cf` отсутствует, последний принятый GitHub Release — `v0.3.10`.
- Открытые контрольные [#1023](https://github.com/Maximiliamos/torgi/issues/1023), [#1021](https://github.com/Maximiliamos/torgi/issues/1021), [#1049](https://github.com/Maximiliamos/torgi/issues/1049).

## Результат аудита production, без персональных данных

| Показатель | Значение | Правильная интерпретация |
|---|---:|---|
| MapDataset | 56 378 точек | P1 подтверждает совпадение DB eligibility и S3/dataset counts, **не** отдельную приёмку источников |
| CDTRF | 1 408 всего; 1 354 active; 54 archived | 1 408 не является числом активных primary |
| МЭТС | 2 188 всего; 1 216 active; 30 archived; 942 unknown | Важна проверка статуса даже при наличии цены |
| Sistematorg | 3 361 всего; 150 active; 3 200 archived; 11 unknown | Строка списка — процедура; число не доказывает 3 361 индивидуальный лот недвижимости |
| Старые stale-active | TBankrot 8 559; Torgi Russia 18 127 | Всего 26 686; сохраняется риск неактуальных проекций |
| Active non-duplicate без цены | 25 691 | Общесистемная ошибка полноты, не только Sistematorg |
| Active non-duplicate без региона | 66 053 | Требуется provenance, нельзя назначать регион приблизительно |
| Активных без GEO | 43 629 | Из них 13 721 без подтверждённого кадастра или адреса |
| Списков новых ЭТП | 21 профиль, 16 runnable, 5 blocked | Лишь 3/16 имеют production-записи; **2/16** успешны в последней общей кампании |

Актуальность: цифры выше — один сохранённый P1-снимок на 02.11.2026; новый импорт или изменение состояния обновляет знаменатели.

## Шесть поручений — отслеживание исполнения

| Направление | Созданный PR / доказательство | Статус и обязательный остаток |
|---|---|---|
| 1. CDTRF | [#1060](https://github.com/Maximiliamos/torgi/pull/1060), merge `c5a0a2ec` | **Merged in main**, read-only обход допускает изменение live total и сохраняет coverage gap. Всё ещё нужны P11, полный source+GEO+map run, сверка по 4 статусам и выборка 30 карточек |
| 2. Sistematorg | [#1061](https://github.com/Maximiliamos/torgi/pull/1061), [issue #1065](https://github.com/Maximiliamos/torgi/issues/1065) | **Proposed**, разделение процедуры/лота и безопасная read-only проверка цен. 18/3361 — не исправлено; нужен подтверждённый per-lot detail contract и контролируемый price backfill без выдуманных сумм |
| 3. GEO | [#1062](https://github.com/Maximiliamos/torgi/pull/1062) | **Proposed**, повтор занятого lock и работа только по адресному/кадастровому evidence. Нужна проверка live GEO, классификация NSPD/VPN transport vs terminal no-match, интеграция с картой |
| 4. Универсальный контракт ЭТП | [#1063](https://github.com/Maximiliamos/torgi/pull/1063) | **Proposed**, quality counters, honest campaign progress, read-only validation. Полный контракт R6-DATA-001 с Decimal, статусами, датами и provenance — открыт [#1021](https://github.com/Maximiliamos/torgi/issues/1021) |
| 5. Качество / безопасность / performance | P1 #41366827021, P13/P16 read-only, #1061/#1063 | **Audit performed, remediation incomplete**. Нужны доказанные исправления stale/unknown, dedupe/sale-only gate, восстановления, нагрузки и управляемая historical repair; не архивировать массово по неполным source runs |
| 6. ROADMAP / контроль | Этот документ и актуализированный ETP status | **Proposed** evidence matrix. Обновлять только по merge SHA, green CI, exact-SHA production run, DB/GEO/S3/WEB proof. Без таких артефактов не писать `done` |

`Proposed` означает открытый PR, а не выполненную поставку. Порядок закрытия зависит от успешного CI и source-specific live evidence. Для отдельных источников нужны повторные проверки до признания `regular_sync_enabled`.

## Следующий порядок принятия

1. CI и review каждого PR по отдельности; не объединять непроверенные изменения в один большой релиз.
2. Подтвердить текущий развёрнутый точный SHA, restore-verified backup и P11. Не обходить release gates.
3. ЦДТ: read-only полный каталог и объяснение каждого исключения (каталог → property candidate → verified detail → persisted → geocoded → public point). Несоответствие 3 147 каталожных строк и 1 408 записей без такой матрицы не считается устранённым.
4. Sistematorg: **не** присваивать стоимость всей процедуры одному лоту; подтвердить связку trade ID + lot ID; повторно импортировать цену только для verified per-lot detail.
5. GEO: ограниченная очередь, причина для каждого негеокодируемого объекта, никакой подстановки центроида области вместо кадастра.
6. Закрывать R1–R5, R6, R7 и последующие волны R8 исключительно после real DB→S3→WEB и браузерных проверок. Исполнить финальные 5 контрольных ошибок и 100 независимых лотов R11.

## Критерии публичного заявления «всё выполнено»

Требуются два независимых полных успешных source-run по каждой принятой ЭТП, достоверные идентичности отдельных лотов, цены/статусы, GEO и конкретные точки публичной карты; отсутствуют ложные дубли и чужие координаты, есть exact-SHA production approval, проверка rollback, source freshness и эксплуатационные метрики. Ни один из этих критериев не подтверждается только созданием PR, зелёным unit CI или регистрацией 22 профилей.

## Дополнение к аудиту: 02.11.2026, 11:30 UTC

- [#1060](https://github.com/Maximiliamos/torgi/pull/1060) слит в `main` как `c5a0a2ec`. Это **только код read-only диагностики**, не production-acceptance ЦДТ. Следующий Home/REG.RU deploy и exact-SHA P1/P11 ещё требуют подтверждения.
- [#1067](https://github.com/Maximiliamos/torgi/pull/1067) предложен P0 fail-closed guard для full-source non-reconciling scans. Причина: прежний Sistematorg сообщил `success` на ровно 500/500 страниц без доказанного конца выдачи. Новый код различает ограниченный preview и заявленный полный импорт; PR ещё не принят.
- [#1068](https://github.com/Maximiliamos/torgi/pull/1068) — read-only DNS/TLS диагностика пяти штатных источников. [Phase 3 Health #41425699370](https://github.com/Maximiliamos/torgi/actions/runs/41425699370) показал **0/5 operational**: три TLS-сбоя, read timeout ГИС и validation failure TBankrot. Не объявлять сетевую деградацию исправленной на основании CI.
- [S5 readiness #41422495215](https://github.com/Maximiliamos/torgi/actions/runs/41422495215) не пройден из-за отсутствия latest exact-SHA P1, а не ошибки парсера. [DR restore #41422096668](https://github.com/Maximiliamos/torgi/actions/runs/41422096668) success.
- [Issue #1049](https://github.com/Maximiliamos/torgi/issues/1049) дополнен конкретными production evidence и безопасной процедурой диагностики. Требуются запуск диагностики на фактическом Home и green source freshness; команда импорта остаётся под ручным операторским допуском.
