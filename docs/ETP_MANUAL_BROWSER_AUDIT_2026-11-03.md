# Ручная проверка 22 ЭТП — 3 ноября 2026

Проверка выполнена по 22 страницам, которые владелец проекта загрузил без VPN,
и по сохранённым HTML/скриншотам V6. Это проверка контрактов источников, а не
разрешение production-записи. Полный импорт, GEO и публикация карты не запускались.

## Итог

**Общий no-VPN write-run пока запрещён.** Страница, которая открылась в браузере,
ещё не доказывает корректную последнюю страницу, уникальную идентичность каждого
лота, цену из detail и безопасное завершение полного обхода.

| Источник | Что подтверждено вручную | Оставшийся блокер полного обхода |
|---|---|---|
| M-ETS | 3 088 лотов, 155 страниц, lot URL и detail | Повторить terminal-page; исправлена подмена цены задатком |
| Lot-online | 4 456 активных объектов, 96/страницу, lot URL | Доказать terminal page текущего API-run |
| CDTRF | 3 086–3 087 live, 155 страниц, 4 статуса и 7 категорий | Полный detail acceptance и сверка всех rejected |
| NISTP | В одной процедуре несколько `#lotN` | Per-lot detail isolation/цена |
| Alfalot | LotCard identity | Объединить upcoming и current; сохранённая page 2 повторяется |
| RTS-tender | UUID trade + UUID lot, Next/Last | Сохранить HTTP/API pagination; старый numeric regex неверен |
| Roseltorg | Procedure/lot identity | Реальный load-more/API и фильтр недвижимости |
| Сбербанк-АСТ | Официальный JSON и точные lot URL | Последняя страница; старый снимок page 2 повторяется |
| Fabrikant | Процедура может содержать несколько лотов | Выделить lot number/detail; procedure ID не является lot ID |
| ARBбитЛот | Отдельные lot URL, ASP.NET paging | Terminal-page proof |
| ETPU | Отдельные lot URL, ASP.NET paging | Terminal-page proof |
| Sistematorg | Trade rows и число лотов | Per-lot detail/цена; trade ID не является lot ID |
| Centerr | Отдельные lot URL, ASP.NET paging | Terminal-page proof |
| Аукцион-центр | 498 страниц, multi-lot procedure | Per-lot detail/цена |
| Profit | Отдельные trade/lot URL, 2 668 страниц | Terminal-page proof |
| AUSIB | 1 155 страниц, multi-lot procedure | Per-lot detail/цена |
| Вердиктъ | `trade#lot-N`, цена/кадастр в строке | Per-lot detail isolation и terminal page |
| uTender | Публичная страница не открылась | `ERR_SSL_VERSION_OR_CIPHER_MISMATCH` |
| RUSSIA OnLine | Отдельный `lot_nid` | Terminal-page proof |
| Electro-torgi | 651 страница, статусы/суммы/описания | Exact HTTP paging и per-lot detail |
| ZakazRF | Отдельные lot URL, ASP.NET paging | Фильтр активной недвижимости и terminal page |
| UTPL | Отдельные lot URL, ASP.NET paging | Terminal-page proof |

## Исправления этого аудита

- NISTP и Вердиктъ сохраняют составную идентичность `торги + номер лота`.
- Sistematorg не присваивает цене отдельного объекта сумму всей процедуры.
- Fabrikant, Аукцион-центр, AUSIB и Electro-torgi разбираются как много-лотовые
  процедуры; generic fallback с потерей второго лота запрещён.
- M-ETS берёт сумму, структурно связанную с подписью `Начальная цена`, а не
  последующий задаток.
- Для источников с общей detail-карточкой отключено ложное обещание `fetch_lot`,
  пока не доказано выделение конкретной секции лота.

Машинный отчёт сохранён локально как
`D:\BankrotAI\etp-operator-logs\manual-etp-parser-audit-20261103-v6.json`.
Raw evidence не публикуется: оно может содержать персональные данные.

## Следующий безопасный шаг

Сделать новый no-VPN read-only capture только для контрактов, которых не хватает:
RTS API/pagination, Electro page 2/last/detail, Fabrikant/Aukcioncenter/AUSIB
multi-lot detail, Roseltorg load-more, Alfalot current, Sberbank last page и
uTender TLS. После этого — повторные fixtures/tests, затем два read-only полных
прохода. Лишь после совпадения счётчиков разрешать import → GEO → map.
