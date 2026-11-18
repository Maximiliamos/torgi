# Матрица 27 источников: фактические границы аудита

Это диагностика сохранённых read-only проверок 17–18 ноября 2026, а не сертификат полной работоспособности. PASS относится только к проверенной выборке. NOT_TESTED не заменяется предположением. Полный production-проход, GEO и публикация требуют отдельных доказательств.

| Источник | Каталог/транспорт | Detail identity | Detail цена | Detail статус | Первое препятствие |
|---|---|---|---|---|---|
| alfalot.ru | BLOCKED | NOT_TESTED | NOT_TESTED | NOT_TESTED | transport / provider_access_or_http |
| arbbitlot.ru | PASS | PASS | PASS | PASS | initial_or_current_price / missing_individual_prices_in_sample |
| aukcioncenter.ru | PASS | PASS | FAIL | PASS | initial_or_current_price / missing_individual_prices_in_sample |
| ausib.ru | BLOCKED | NOT_TESTED | NOT_TESTED | NOT_TESTED | transport / provider_access_or_http |
| bidexpert.ru | PASS (выборка) | NOT_TESTED | NOT_TESTED | NOT_TESTED | Detail и полный проход этой проверкой не подтверждены |
| cdtrf.ru | PASS | NOT_TESTED | NOT_TESTED | NOT_TESTED | В выборке не зафиксировано; полный проход не доказан |
| centerr.ru | PASS | PASS | PASS | PASS | В выборке не зафиксировано; полный проход не доказан |
| electro-torgi.ru | BLOCKED | NOT_TESTED | NOT_TESTED | NOT_TESTED | transport / profile_explicitly_blocked |
| etp-profit.ru | BLOCKED | NOT_TESTED | NOT_TESTED | NOT_TESTED | transport / tls_certificate_validation |
| etpu.ru | PASS | PASS | PASS | PASS | initial_or_current_price / missing_individual_prices_in_sample |
| fabrikant.ru | BLOCKED | NOT_TESTED | NOT_TESTED | NOT_TESTED | transport / provider_access_or_http |
| fedresurs.ru | BLOCKED | NOT_TESTED | NOT_TESTED | NOT_TESTED | EFRSB credentials are required: set FEDRESURS_LOGIN and FEDRESURS_PASSWORD |
| lot-online.ru | PASS | PASS | N/A | N/A | В выборке не зафиксировано; полный проход не доказан |
| m-ets.ru | PASS | PASS | PASS | FAIL | auction_status / unknown_status_in_sample |
| nistp.ru | PASS | PASS | PASS | PASS | В выборке не зафиксировано; полный проход не доказан |
| roseltorg.ru | PASS (18.11, TORGI UA) | PASS (выборка) | PASS (выборка) | PASS (выборка) | Поля каталога неполны; полная пагинация не доказана |
| rts-tender.ru | BLOCKED | NOT_TESTED | NOT_TESTED | NOT_TESTED | transport / profile_explicitly_blocked |
| rus-on.ru | PASS | PASS | PASS | PASS | initial_or_current_price / missing_individual_prices_in_sample |
| sberbank-ast.ru | PASS | PASS | FAIL | FAIL | individual_detail_price / missing_price_in_fetched_detail |
| sistematorg.com | PASS | PASS | PASS | PASS | initial_or_current_price / missing_individual_prices_in_sample |
| tbankrot.ru | BLOCKED | NOT_TESTED | NOT_TESTED | NOT_TESTED | TBankrot session cookie file is unavailable or invalid |
| torgi-russia.ru | PASS (выборка) | NOT_TESTED | NOT_TESTED | NOT_TESTED | Detail и полный проход этой проверкой не подтверждены |
| torgi.gov.ru | PASS (выборка) | NOT_TESTED | NOT_TESTED | NOT_TESTED | Detail и полный проход этой проверкой не подтверждены |
| utender.ru | BLOCKED | NOT_TESTED | NOT_TESTED | NOT_TESTED | transport / profile_explicitly_blocked |
| utpl.ru | PASS (18.11, TORGI UA) | PASS (выборка) | PASS (выборка) | PASS (выборка) | Поля каталога неполны; полный проход не выполнен |
| vertrades.ru | PASS | PASS | PASS | PASS | property_classification / unclassified_sample |
| zakazrf.ru | PASS | PASS | PASS | PASS | initial_or_current_price / missing_individual_prices_in_sample |

## Более поздние подтверждения и незакрытая работа

- M-ETS: отдельный полный read-only проход 5070 карточек, 5018 приняты, 52 отклонены. Это расширяет прежнюю двухстраничную выборку, но не заменяет два production-прохода.
- Vertrades: два полных read-only каталога по 1144 уникальных ID; проверена выборка 30 деталей. Sberbank: полный read-only каталог 71 объект/18 страниц.
- Sistematorg отвечает в сохранённой проверке: две страницы, detail identity/price/status PASS в выборке. Отсутствие цены/статуса в части каталоговых карточек остаётся отдельным ограничением.
- TBankrot: 181 публичная карточка проверена и исправлена; HTTP 429 остановил массовый сбор. Обычный вход не открывает платные даты. Нужны точные первичные ETP identities, а не соединение по одному кадастру.
- Fedresurs: production credentials отсутствуют. Установка фиктивного success или обход доступа не применяется.
- Плановый production run 770e259c стартовал 18 ноября: ГИС и lot-online завершили свои стадии; общий run на момент записи ещё выполняется. Torgi Russia упал на JSON datetime; исправление подготовлено в #1255 и требует внедрения и повторного прохода.
- roseltorg/utpl: транспортные отказы; etp-profit: TLS. alfalot/ausib/fabrikant: access error. Это реальные незакрытые ограничения, не parser PASS.
- electro-torgi/rts-tender/utender отмечены blocked в более поздней manual QA. Не включать их массовый импорт без положительного подтверждения контракта и доступа.
- Нужны точный итоговый SHA, P1/P11, два последовательных прохода Sberbank→Vertrades→M-ETS, затем контролируемый семиисточниковый production pipeline.

## Артефакты

Дополнение 18 ноября 07:15 UTC: повторные прямые запросы Roseltorg и UTPL
вернули HTTP 200 как на Windows, так и в production Linux. Прежний
ConnectionError не является постоянным доказательством поломки парсера.
Профит отвечает на Windows, но в Docker не хватает intermediate GlobalSign.
Issuer chain проверена существующим root bundle; добавление intermediate
в SSL context даёт HTTP 200 с проверкой hostname. Патч Docker trust bundle
подготовлен в #1255. Эти транспортные проверки не доказывают весь каталог.

Приватные файлы: `.artifacts/finish/all-etp-stage-audit.log`, `core-five-deployed-audit.log`, `etp-dcd-manual-qa/etp-manual-qa-all.json`. В матрицу не включены cookies, credentials или исходные персональные данные.

Последующая проверка выявила, что Session по умолчанию сохранял
python-requests User-Agent: setdefault не устанавливал предусмотренный
идентификатор TORGI. После явной установки честного TORGI User-Agent
Roseltorg/UTPL проходят transport и выборочный detail identity/price/status.
Свой явно заданный User-Agent вызывающего кода сохраняется. Каталожные
поля и полная пагинация не объявлены исправленными этим сетевым патчем.
У Профит после TLS-доступа выявлено пересечение ID соседних страниц;
оно остаётся незакрытым ограничением, не обходится ослаблением проверки.
