# P2: локальная проверка производительности и GitFlic

На домашнем ПК TORGI остаётся основным сервером; эти инструменты не переносят production.

## Замер задержек read-only API

В локальном окружении проекта:

```powershell
python scripts/benchmark_local_api.py --count 10 --output local-api-latency.json
```

Проверяются локальные HTTP пути live, ready и текущей карты на порту 18000.
Ограничение 50 запросов на URL и пауза между ними защищают сервер от нагрузочного теста. Запрещены внешние домены, query-параметры, credentials и редиректы. Получаем median/p95/max и коды HTTP для сравнения до/после оптимизации. Нельзя называть ускорением изменения без двух измерений на сопоставимой нагрузке.

Измерение SQL запросов отдельно: `EXPLAIN (ANALYZE, BUFFERS)` только на безопасной тестовой базе или согласованном read-only запросе; произвольные тяжёлые EXPLAIN на production не выполнять.

## GitFlic

1. Создать **пустой** российский GitFlic-репозиторий вручную в собственном аккаунте.
2. В локальном клоне проверить dry-run (ничего не меняет):

```powershell
.\scripts\gitflic-backup.ps1 -GitFlicUrl 'https://gitflic.ru/project/OWNER/REPO.git'
```

3. После проверки доступа разрешить фактический push:

```powershell
.\scripts\gitflic-backup.ps1 -GitFlicUrl 'https://gitflic.ru/project/OWNER/REPO.git' -Apply
```

Скрипт не перезаписывает чужой remote, сначала вызывает `git push --dry-run`, потом передаёт `main` и теги. GitHub Issues, Pull Requests, Actions, Secrets и production данные в GitFlic таким способом **не переносятся**. Ключи доступа не включать в URL.

## Локальные тесты

```powershell
python scripts/check_module_sizes.py
python -m pytest -q tests/test_local_api_benchmark.py
python -m pytest -q tests/test_operational_quality.py tests/test_map_delivery_reconciliation.py
```
