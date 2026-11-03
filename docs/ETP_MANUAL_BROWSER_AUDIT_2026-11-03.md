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

### Повторный разбор сохранённых страниц

После ручной проверки все четыре доступных no-VPN набора повторно пропущены через
актуальный код. Найдены и устранены дефекты, которые не покрывал первоначальный
синтетический набор:

- M-ETS больше не склеивает `Лот №1` с расположенной рядом суммой: каталожные
  `300 000 000`, `1 479 500` и `4 181 884` RUB совпадают со структурой карточек;
- AUSIB/Аукцион-центр не связывают ссылку первой процедуры с `Лот №1/№2` из
  другой строки; одиночная процедура без явной надписи `Лот №1` сохраняется как
  стабильная пара `procedure:1`;
- Centerr исключает редакционные ссылки из блока новостей, даже если их URL
  выглядит как lot URL; сохранённые страницы теперь дают ровно 20 строк, а не
  20 строк плюс рекламируемый старый объект;
- Fabrikant разбирает серверные React Flight chunks без исполнения JavaScript и
  использует `lots_view_info_id` как ID лота; на сохранённой странице найдены
  четыре объекта недвижимости с отдельными ценами и кадастровыми номерами.

Машинный итог: 14 из 21 новых профилей имеют распознанные сохранённые каталоги;
Alfalot, Roseltorg и Сбербанк-АСТ имеют повтор page 1/page 2; общий V6 capture
CDTRF не содержит подтверждённых lot links; RTS, uTender и Electro остаются
transport/evidence-blocked. Lot-online — отдельный действующий baseline connector
и не входит в эти 21 новых профилей. Это всё ещё не terminal-page acceptance.

Полный regression run: 1115 passed, 12 skipped. Два несвязанных локальных сбоя
зафиксированы отдельно: сравнение naive local time с UTC lease и установленный в
окружении пакет `bankrotai-finder 0.2.0` при metadata проекта `0.3.10`; parser
набор `tests/test_etp_sources.py` проходит полностью.

Машинный отчёт сохранён локально как
`D:\BankrotAI\etp-operator-logs\manual-etp-parser-audit-20261103-v6.json`.
Raw evidence не публикуется: оно может содержать персональные данные.

## Следующий безопасный шаг

Сделать новый no-VPN read-only capture только для контрактов, которых не хватает:
RTS API/pagination, Electro page 2/last/detail, Fabrikant/Aukcioncenter/AUSIB
multi-lot detail, Roseltorg load-more, Alfalot current, Sberbank last page и
uTender TLS. После этого — повторные fixtures/tests, затем два read-only полных
прохода. Лишь после совпадения счётчиков разрешать import → GEO → map.

## Постоянный двухкомпьютерный контур

- ПК №1: разработка, GitHub и тесты с VPN; production остаётся только здесь.
- ПК №2: без VPN, только Python + Playwright Chromium и read-only collector.
- Обмен выполняется ZIP-файлами напрямую через AnyDesk. В пакет не входят
  `.env`, PostgreSQL, Docker, SSH/API-ключи или production-конфигурация.
- Фазы приёмки: `pilot` (M-ETS, CDTRF, RTS) → `problem` (9 площадок) → `all`.
- Каждый источник принимается только при доказанных page 1/page 2/last,
  индивидуальном detail, стабильном lot ID, цене/статусе и двух совпавших
  полных read-only проходах. Unit-тест сам по себе приёмкой не является.

Пакет узла создаётся `scripts/build-etp-audit-node-package.ps1`, автономный
запуск на ПК №2 выполняет `scripts/run-etp-audit-node.ps1`. Результаты и логи
сохраняются в `C:\ETP_AUDIT`, без обращения к production DB/GEO/map.
