# Операторский ручной прогон: 7 штатных источников → PostgreSQL → GEO → MapDataset/S3

Скрипт: [scripts/run-standard-sources-geo-map-no-vpn.ps1](../scripts/run-standard-sources-geo-map-no-vpn.ps1).

**Ветка ops/standard-seven-sources-geo-map-20261106 — только операторский скрипт; не включать автоматически в scheduler.** Это не импорт 18+/22 ЭТП. Список: torgi.gov.ru, lot-online.ru, torgi-russia.ru, bidexpert.ru, m-ets.ru, cdtrf.ru, nistp.ru. TBankrot должен оставаться штатно paused. Alfalot и остальные не добавляются.

**Условия до выполнения:** выполнить на Home при полностью выключенном VPN; GH CLI и Docker доступны, три worker image совпадают с ожидаемым live main SHA; DNS без Fake-IP 198.18/15; все 7 источников настроены и не paused/circuit-blocked; нет активного lease; NISTP, CDTRF и M-ETS остаются строго 5-page и reconcile_missing=False, NISTP 2 detail attempts/page. Скрипт прервёт запуск до импорта при несовпадении.

**Действия:** делает новый dump PostgreSQL с изолированным restore; ставит стандартную durable Celery nationwide full задачу (реальные INSERT/UPDATE и штатные политики dedup/expiry, это НЕ read-only); проверяет результаты 7 LotSyncSourceRun и нулевое missing-lot архивирование новых ЭТП; выполняет bounded pending-lot GEO на отдельном worker; запускает штатный immutable MapDataset builder; проверяет current ready dataset и публичный REG.RU S3 manifest. Transcript — D:\BankrotAI\etp-operator-logs.

**Не обещает:** автоматического снятия pause/circuit; полного содержимого НИСТП/МЭТС/ЦДТ (их specs ограничены 5 страницами); 100% GEO всех записей (по умолчанию максимум 16 × 250 batch items); гарантированную метку на карте каждому лоту без достоверных координат. Если обнаружен неуспешный источник — GEO/карта НЕ запускаются из этого сценария. Предыдущие production operations не отменяются автоматически. Режим -ConfirmProductionWrites обязателен.

**Ревью:** скрипт содержит пять встроенных Python-фрагментов; их синтаксис проверен вне production. Операторский прогон на Home **не выполнялся** при публикации этого файла, поэтому не утверждать успешную DB/GEO/S3 приёмку. После запуска сохранить evidence RUN_ID, каждую source-row, GEO_SUMMARY и MAP_CURRENT. Исторические блокеры НИСТП (#1115/#1116) остаются, а partial 5-page run не доказывает source-wide acceptance.

**Запуск из скачанного файла:** передавать -ExpectedSha текущего развёрнутого и проверенного main, -BackupScript указывающий на проверенную копию scripts/backup-home-postgres.ps1 и -ConfirmProductionWrites. Скачивание следует делать из неизменяемого commit SHA, не из плавающей ссылки main/ветки.