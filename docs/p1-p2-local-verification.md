# P1/P2 — локальная проверка без влияния на production

**Режим read-only.** PostgreSQL, карта REG.RU S3, процессы на домашнем ПК и настройки GitHub не меняются.

## 1. Актуальность среды

Текущая `main` на момент аудита содержит Redis/Celery и Cloudflare в README, `pyproject.toml` и workflow. Владелец сообщает, что Redis уже выведен из эксплуатации. Сначала сверить текущую ветку с фактически развёрнутым кодом на Home. **Не восстанавливать Redis и не удалять зависимости вслепую.**

## 2. Качество торгов и карты

В локальном окружении с установленными зависимостями:

```powershell
python -m pytest -q tests/test_operational_quality.py tests/test_source_freshness.py tests/test_persistent_ingestion.py tests/test_map_delivery_reconciliation.py tests/test_region_sanity.py tests/test_map_current_geo.py
```

`scripts/p1-production-audit.ps1` читает Home-базу и опубликованный manifest. Он не исправляет координаты и статусы. В текущем скрипте ненулевой `warning_count` **не** приводит к неуспеху: этот показатель требует отдельной оценки.

Тесты не подтверждают полноту источников без контрольных выгрузок. Перед правками нужны первичные идентификаторы и результаты полных обходов. Никакого массового изменения статусов, архивов или дедупликации без доказательств.

## 3. Независимость от зарубежных сервисов

```powershell
python scripts/audit_external_dependencies.py --output local-external-domains.json
```

Сканер не выполняет сетевых запросов. Он находит буквальные домены в коде и конфигурации. `foreign` — известный иностранный поставщик; `review` — требует ручной проверки. Местоположение инфраструктуры по TLD не определяется. Динамические URL, реальные DNS/CNAME и сетевой трафик инструмент не покрывает.

Только после согласованной миграции можно включить строгий режим:

```powershell
python scripts/audit_external_dependencies.py --fail-on-foreign
```

До устранения известных внешних зависимостей **не включать** строгий режим в CI.

## 4. Локальные проверки

```powershell
python scripts/check-repository-consistency.py
python scripts/check_module_sizes.py
python -m pytest -q tests/test_external_dependency_inventory.py
```

Измерять производительность и менять индексы только после фиксации текущего baseline; этот PR не ускоряет поиск сам по себе.

## 5. Российское зеркало Git

Создать пустой репозиторий GitFlic вручную, после чего в локальном клоне:

```powershell
git remote add gitflic https://gitflic.ru/project/OWNER/REPO.git
git remote -v
git push --dry-run gitflic main
# После проверки URL и прав:
git push gitflic main
```

Заменить `OWNER/REPO` на точное имя созданного проекта. Публикация и работа зеркала пока не подтверждены. Обычный `git push` не копирует GitHub Issues и Pull Requests.
