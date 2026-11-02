# TORGI — отдельный аудит навигации и ссылок 22 ЭТП

Дата сверки: 2026-11-02. Основа: main 3471b4a, изменения PR #1069.
Статус: проверены доступные публичные HTML-страницы и поисковые копии, а также текущий код. Прямые запросы из контейнера недоступны из-за DNS. Часть полученных страниц — кэш июля–октября 2026. Реальные JS/POST-запросы пагинации не выдавать за подтвержденные без браузерных трасс.

Градации: П = публичный интерфейс/ссылка наблюдались; К = механизм известен только из исходников; Н = не удалось подтвердить. Названия и расположение элементов зафиксированы относительно доступного представления страниц; пиксельные координаты выдаёт браузерный снимок, а не текстовый поиск.

## Площадки

1. **ЦДТ (cdtrf.ru)** — https://torgi.cdtrf.ru/?Declare=true&RecieveReq=true . П: сверху «Поиск лотов», блок «Фильтры», «Применить фильтры», «Очистить фильтры», количество 20/50/100; видны карточки торгов с ценами и статусом. К: PageSize/PageNum, detail /trades/{id}, спецкраулер. Н: фактический запрос страницы 2 и доказательство отдельного ID каждого лота. Проверить PageNum=2/last, несколько лотов и detail-цену.

2. **МЭТС (m-ets.ru)** — https://m-ets.ru/search . Прямой запрос каталога ответил 403. К: q-категории, page=..., карточка /{trade}-{lot}, специализированный MetsConnector. Н: навигационные кнопки, текущий HTTP-контракт, последняя страница. В общем импорте использован иной generic-коннектор: унифицировать после capture.

3. **НИСТП (nistp.ru)** — https://nistp.ru/bankrot/trade_list.php . П: в поиске переключатель «Способ вывода: Торги / Лоты», кнопки «Поиск / Очистить», «Страница: 1…10» непосредственно над таблицей; одна процедура включает ряд «Лот №». П: клик по процедуре/лоту указывает /bankrot/trade_view.php?trade_nid=... (детальный HTTP fetch недоступен). К: POST формы nodesearch с pagenum. Н: POST payload из браузера. Не подменять лоты процедурами.

4. **Альфалот (alfalot.ru)** — https://etp.alfalot.ru/app/LotList/page?LotList.pageNum.lots=0&lotFilterState.activeFilter=SUPCOMING&lotFilterType.activeFilter=SSALE . П: «Торговый зал» с вкладками «Предстоящие / Текущие / Приостановленные»; таблица со столбцом «Номер лота», цены. К: /app/LotCard/page?LotCard.lotEntity=LT%3A..., pageNum.lots=0. Н: реальный запрос следующей страницы; ранее generic выдавал ложный конец. Исправлено: отсутствие подтверждённой пагинации теперь ошибка.

5. **РТС-тендер (rts-tender.ru)** — https://it2.rts-tender.ru/v2/grid/trades/all . Н: UI / сеть не доступны; К: Angular grid и возможный маршрут /trades/lotCard/{uuid} из capture-скрипта. Нужны доказанные XHR, фильтры по имуществу и per-lot identities. Не обходить защиту.

6. **Росэлторг (roseltorg.ru)** — https://www.roseltorg.ru/search/sale ; отдельно https://178fz.roseltorg.ru/ . Запрос каталога завершился таймаутом. К: /procedure/... из профиля. Н: UI, раздел банкротства против приватизации, листание. Исправлено: generic не считает одну страницу полным обходом без контракта.

7. **Сбербанк-АСТ (sberbank-ast.ru)** — https://www.sberbank-ast.ru/ESGoodsList.html . Внешний HTML недоступен. К: JSON POST /api/Processing/main, pagenum от нуля, pagesize=21, PurchaseList и Total, detail https://utp.sberbank-ast.ru/Catalog/NBT/GoodsView/... . Н: реальная browser section sourceTerm, правильность API схемы, переход и последняя страница. Требуется отдельная проверка RUB и принадлежности к банкротству.

8. **Фабрикант (fabrikant.ru)** — https://www.fabrikant.ru/procedure/search/sales?section_ids%5B%5D=6 . Веб-проверка завершилась таймаутом. К: GET page_number, карточки /v2/trades/procedure/view/{token}. Н: действующий фильтр банкротства, page2 и ID самостоятельного лота.

9. **АРБбитЛот (arbbitlot.ru)** — https://torgi.arbbitlot.ru/public/purchases-all/ . П: левое меню «Аукционы / Конкурсы / Публичные предложения / Все торги», поля поиска над таблицей, строка «Страницы: 1…10 >>» сверху результатов; отдельные колонки № торгов, № лота и цена. П: detail /public/{sale_type}/lots/view/{id}/. К: ASP.NET postback с __VIEWSTATE. Н: POST eventtarget, завершение всех страниц.

10. **Уральская ЭТП (etpu.ru)** — https://bankrupt.etpu.ru/public/purchases-all/ . П: то же меню разделов общего движка и каталог. К: /public/{sale_type}/lots/view/{id}/, ASP.NET postback. Н: реальные запросы кнопки «2», сохранение фильтров, последний лист; проверить per-lot цены.

11. **Sistematorg (sistematorg.com)** — https://sistematorg.com/tradelist.php . П: «Поиск торгов», «Реестр торгов», «Страница: 1…10» НАД таблицей. Процедура 19431-ОАОФ содержит авто и земельный участок. К: /trade_view.php?trade_nid={id}, POST tradesearch/pagenum. Исправлено: процедура разделяется на кандидатные ID source:trade_nid:lot_number, чужие цены/статусы не присваиваются, неизвестные остаются NULL/unknown; fetch_lot по общей процедуре блокируется. Н: per-lot detail, безопасная миграция старых записей, реальный page2.

12. **Центр реализации (centerr.ru)** — https://bankrupt.centerr.ru/public/purchases-all/ . П: поисковая форма выше таблицы; «Страницы: 1…10 >>», отдельная колонка цены. П: /public/{sale_type}/lots/view/{id}/. К: __doPostBack. Н: истинный eventtarget/последний лист; проверить цену только из отдельного лота.

13. **Аукцион-центр (aukcioncenter.ru)** — https://aukcioncenter.ru/etp/trade/list.html?show=current . П: форма «Поиск торгов» с «Номер торгов / Номер лота / Предмет / Состояние». К: page, detail /trade/view/purchase/general.html?id=... . Н: этот detail — процедура либо лот; повторные страницы. Не импортировать процедуру как лот без ID.

14. **Вердиктъ (vertrades.ru)** — https://arest.vertrades.ru/bidding . П: поля поискового фильтра и таблица № торгов, № лота, цена, статус; часть выдачи — старые архивные торги 2022 года. П: https://arest.vertrades.ru//bidding?bidding_type=1 . К: /showTrades?id=..., GET page. Н: текущая секция банкротства, per-lot detail и последняя страница.

15. **RUSSIA OnLine (rus-on.ru)** — https://rus-on.ru/bankrot/trade_list.php . П: «Реестр торгов», «Поиск / Очистить», «Страница: 1…10» над таблицей; отдельная ссылка на процедуру и на каждый лот. П: /bankrot/lot_view.php?lot_nid=... . К: POST tradesearch/pagenum. Н: page2 payload и детали цены. Использовать lot_nid, не общий trade_nid.

16. **ЗаказРФ (zakazrf.ru)** — https://bankrot.zakazrf.ru/public/purchases-all/ . П: поля № торга/лота, цена/статус и «Страницы: 1…10 >>»; отдельные лоты одной процедуры. П: /public/{sale_type}/lots/view/{id}/. К: ASP.NET postback. Н: реальный POST и проверка расписаний по каждой карточке.

17. **Объединённая торговая площадка (utpl.ru)** — https://bankrupt.utpl.ru/public/purchases-all/ . П: разделы типов торгов слева, фильтры сверху, «Страницы: 1…10 >>». П: /public/{sale_type}/lots/view/{id}/. К: ASP.NET postback. Н: запрос листания, поведение фильтров и даты статусов.

18. **Профит (etp-profit.ru)** — https://etp-profit.ru/etp/trade/list.html . П: форма «Поиск торгов» по номеру торгов/лота, предмету, датам и статусу. П: индексируемые URL ?page=409, ?page=519 доказывают существование параметра page, НЕ полноту текущей выдачи. Исправлено: заменён old.etp-profit.ru на официальный маршрут и пояснено состояние blocked. Н: per-lot href и действующий полный контракт пагинации.

19. **Аукционы Сибири (ausib.ru)** — https://ausib.ru/ . Н: каталог, кнопки, адрес лота и пагинация не получены. Не заменять отсутствие доказательств выдуманным endpoint; blocked.

20. **uTender (utender.ru)** — https://utender.ru/ . Прямой ответ 502. Н: каталог, кнопки и ссылки. Статус blocked, без обхода anti-bot.

21. **Electro-Torgi (electro-torgi.ru)** — https://bankrotstvo.electro-torgi.ru/ . П: на главной «Поиск имущества», «Расширенный поиск», «Торги»; переход «Торги» ведёт на https://bankrotstvo.electro-torgi.ru/lots (HTTP 429). Исправлено: адрес каталога и описание blocked, обход ограничений не предпринимался. Н: список лотов, API, пагинация.

22. **РАД / Lot-online (lot-online.ru)** — https://catalog.lot-online.ru/ . П: основной поиск, «Расширенный фильтр», категории, «Показать лоты», «Сохранить», «Сбросить фильтр», «Карта». Каталог существует отдельно от корня lot-online.ru; К: production connector пользуется маршрутом detail и pagination; Н: полная актуальная source coverage. Сверять категории, кадастровый фильтр, архив, карточки, географию.

## Исправления по результатам сверки

- Снято ложное представление, что пустая/повторённая страница свидетельствует о завершённом каталоге. Для необследованной пагинации парсер возвращает ошибку.
- У Sistematorg устранено смешение идентификаторов процедуры и недвижимых лотов на этапе read-only candidate; per-lot цена/статус намеренно неизвестны. Историческую миграцию пока не выполнять без проверки DB.
- Для Профит и Electro-Torgi скорректированы публичные стартовые URL, но источники НЕ разблокированы.
- Браузерный scripts/etp_capture_v6.py сохраняет рядом со скриншотами *_navigation.json: видимые пункты навигации, направления безопасных ссылок, координаты элементов текущего viewport, признаки ASP.NET/pagenum_send. Повторно пытается открыть вторую страницу реальным UI кликом. Координаты не следует переносить на другие размеры окна.
- Все общие production-импорты новых ЭТП по-прежнему требуют индивидуальной операторской приёмки; тестовые данные не являются сертифицированными лотами.

## Инструкция проверки на Home Windows

1. Подготовить отдельную рабочую копию ветки PR #1069; установить зависимости проекта и Playwright Chromium.
2. Из корня репозитория: set PYTHONPATH=src (PowerShell: $env:PYTHONPATH='src').
3. Запустить: python scripts/etp_capture_v6.py --out etp-evidence --max-details 2 .
4. Для каждого из 22 источников открыть 01_landing.png, 02_catalogue_first.png и 02_catalogue_first_navigation.json. Проверить поиск, разделы и точку пагинации.
5. Проверить 03_catalogue_second_attempt.png, events.json, lot_01.png, lot_02.png: разные ссылки и реальные ID лотов, не единственный ID процедуры.
6. Не публиковать ZIP без обезличивания (содержит исходный HTML и персональные данные). Собрать полные page/detail evidence и staging DB/map; сравнить через python -m bankrotai.services.etp_22_acceptance_audit --input evidence.json --output report.json.
7. Не включать cron/архивирование, пока все 22 не пройдут отдельно source acceptance по docs/ETP_22_ACCEPTANCE_AND_LOT_VERIFICATION.md и точный-SHA CI.

## Ограничение аудита

Это НЕ фактический сквозной прогон всех страниц/лотов на официальных площадках на дату аудита. У ряда сайтов HTTP 403, 429, 502, таймауты; HTML-копии могут быть старше даты проверки. Исправления безопасного чтения и контроля качества сделаны в коде; индивидуальная production-приёмка ещё не выполнена.