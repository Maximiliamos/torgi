# Операторский ручной прогон: 7 штатных источников → PostgreSQL → GEO → MapDataset/S3

Скрипт: [scripts/run-standard-sources-geo-map-no-vpn.ps1](../scripts/run-standard-sources-geo-map-no-vpn.ps1).

**Ветка ops/standard-seven-sources-geo-map-20261106 — только операторский скрипт; не включать автоматически в scheduler.** Это не импорт 18+/22 ЭТП. Список: torgi.gov.ru, lot-online.ru, torgi-russia.ru, bidexpert.ru, m-ets.ru, cdtrf.ru, nistp.ru. TBankrot должен оставаться штатно paused. Alfalot и остальные не добавляются.

**Условия до выполнения:** выполнить на Home при полностью выключенном VPN; GH CLI и Docker доступны, три worker image совпадают с явно переданным live main SHA; DNS без Fake-IP 198.18/15; все 7 источников настроены и не paused/circuit-blocked; нет активного lease. NISTP, CDTRF и M-ETS обязаны иметь full selected-catalogue specs (`max_batches=500`, `require_source_end=True`, `reconcile_missing=False`), без per-page detail cap у NISTP. Скрипт прервёт запуск до импорта при несовпадении.

**Действия:** делает новый dump PostgreSQL с изолированным restore; ставит стандартную durable Celery nationwide full задачу (реальные INSERT/UPDATE и штатные политики dedup/expiry, это НЕ read-only); проверяет результаты 7 LotSyncSourceRun и нулевое missing-lot архивирование новых ЭТП; выполняет bounded pending-lot GEO на отдельном worker; запускает штатный immutable MapDataset builder; проверяет current ready dataset и публичный REG.RU S3 manifest. Transcript — D:\BankrotAI\etp-operator-logs.

**Не обещает:** автоматического снятия pause/circuit; полного содержимого провайдера за пределами доказанного выбранного каталога; импорта карточек, не прошедших индивидуальную проверку; гарантированную метку на карте лоту без достоверных координат. По умолчанию GEO ограничен 64 × 500 items и явно сообщает возможный остаток. Если источник неуспешен или новый ЭТП прошёл не более пяти страниц — GEO/карта НЕ запускаются. Предыдущие production operations не отменяются автоматически. Параметры `-ExpectedSha` и `-ConfirmProductionWrites` обязательны.

**Ревью:** скрипт содержит пять встроенных Python-фрагментов; их синтаксис проверяется вне production. После обновления full-catalogue контрактов новый операторский прогон на Home ещё не выполнялся. После запуска сохранить evidence RUN_ID, каждую source-row, GEO_SUMMARY и MAP_CURRENT. Исторические блокеры НИСТП (#1115/#1116) остаются: полный пагинатор не превращает catalogue-only карточки в доказанные индивидуальные лоты.

**Запуск из скачанного файла:** передавать -ExpectedSha текущего развёрнутого и проверенного main, -BackupScript указывающий на проверенную копию scripts/backup-home-postgres.ps1 и -ConfirmProductionWrites. Скачивание следует делать из неизменяемого commit SHA, не из плавающей ссылки main/ветки.
