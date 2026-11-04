# TORGI / STERDEZ — GitHub PR и Issues: инвентаризация и решения, 04.11.2026

**Основание:** проверка реальных REST данных GitHub `pulls?state=open`, `issues?state=open`, файлов каждого PR, основного дерева кода, PR CI и журналов пяти Home/Web workflow. **На сервер Home через SSH/RDP доступа нет, а sterdez.online из доступного веб-окружения не открылся.** Факты о недоступности и неисправности относятся к конкретным датированным Actions; это не утверждение текущего online/offline.

На старте: **17 open PR, 15 самостоятельных open Issues** (GitHub API /issues также включает PR — нельзя посчитать их второй раз). Закрыты как устаревшие **4 PR** без слияния или удаления ветвей: #964, #957, #959, #1064. После создания #1085: **14 open PR** на момент сверки; начатая агентом #1080 остаётся открытой, старые branches/history не удалены. Никакие изменения не объединялись в main без проверки CI.

## PR — решения по каждому

| PR | Тема | Решение и доказательство |
|---|---|---|
| [#1085](https://github.com/Maximiliamos/torgi/pull/1085) | 09:00/21:00 + 22-source QA / map retry | **Держать draft до CI и Home acceptance**. Новый read-only QA всех 21 и версия-safe production enrollment; фикс потери dirty-trigger при ошибке карты. Код не merged/deployed |
| [#1080](https://github.com/Maximiliamos/torgi/pull/1080) | Многолотовые ЭТП и браузерные доказательства | **Приоритет P1, сохранить, ревью**. Действительная работа по M-ETS, Sistematorg, AUSIB, Fabrikant и 22-source audit; CI на окончательном head и полная source acceptance не доказаны; не запускать mass import |
| [#1066](https://github.com/Maximiliamos/torgi/pull/1066) | React/Vite/Playwright/Vitest/ESLint patch | **Сохранить; перебазировать на main, WEB build/E2E/security**, потом решение о merge; старые 2 workflow success не заменяют WEB acceptance после rebase |
| [#1019](https://github.com/Maximiliamos/torgi/pull/1019) | SHA-256 verified source-lot identities preview→commit | **P1 безопасность — приоритетный уникальный PR**. `etp_canary_identity.py` ещё отсутствует в main. Перебазировать, проверить exact IDs/rollback, тогда объединять отдельно |
| [#965](https://github.com/Maximiliamos/torgi/pull/965) | 20 ЭТП + фильтр карты | **Оставить draft для selective extraction**, не объединять весь старый интеграционный дифф. MapFiltersPanel уже совпадает с main; старые parser/capture files устарели; новые уникальные части анализировать отдельно |
| [#960](https://github.com/Maximiliamos/torgi/pull/960) | GitFlic mirror / local API benchmark | **Сохранить как P2**, уникальные скрипты отсутствуют в main; Production reliability старого SHA failed; не отправлять код/ключи в GitFlic без одобрения |
| [#958](https://github.com/Maximiliamos/torgi/pull/958) | SourceLot timestamps, region codes, GEO audit | **Сохранить draft для выборочного переноса**. Часть source-region исправлений уже в main; уникальный read-only аудит отсутствует; старый CI failed |
| [#955](https://github.com/Maximiliamos/torgi/pull/955) | Foreign-service dependency inventory | **P2, сохранить**. Уникальный статический сканер отсутствует в main; старый CI failed |
| [#954](https://github.com/Maximiliamos/torgi/pull/954) | GitHub Actions minor patch | **Блокировать merge**: Production reliability и Cloudflare deploy на PR failed; сначала проверить pins, Actions compatibility, затем новый CI |
| [#951](https://github.com/Maximiliamos/torgi/pull/951) | Pydantic minor dependency range | **Не auto-merge** только по старому зелёному CI: нужен rebase/lock/production model contract |
| [#950](https://github.com/Maximiliamos/torgi/pull/950) | Anthropic SDK major range | **Не auto-merge**, API breaking changes/usage review, не ухудшить независимость от иностранных API; CI на старом SHA не доказывает runtime |
| [#949](https://github.com/Maximiliamos/torgi/pull/949) | Ruff/mypy/PyInstaller patch | **Rebase + Python full suite**, затем рассмотрение merge; старый CI success |
| [#932](https://github.com/Maximiliamos/torgi/pull/932) | P13–P17 public map eligibility policy | **Не merge без controlled DB dry-run и rollback**, уникальные public_map_policy/public_map_quality в main отсутствуют; старый CI success не есть production acceptance |
| [#930](https://github.com/Maximiliamos/torgi/pull/930) | Map OperationProgressCard / old version 0.3.6 | **Оставить draft, нет решения о переносе**; уникальный OperationProgressCard не присутствует в main, смешан с устаревшим version bump. Извлекать UI отдельно, если полезно |
| [#964](https://github.com/Maximiliamos/torgi/pull/964) | старый МЭТС | **Закрыт без merge**; новая версия MetsConnector и её tests уже на main |
| [#957](https://github.com/Maximiliamos/torgi/pull/957) | старый timeout map tile cleanup | **Закрыт**; main содержит усовершенствованные bounded-delete и тесты, старая версия была бы регрессией |
| [#959](https://github.com/Maximiliamos/torgi/pull/959) | старый REG.RU orphan audit | **Закрыт**; в main есть более новая версия planner и тестов |
| [#1064](https://github.com/Maximiliamos/torgi/pull/1064) | старый ROADMAP P1 snapshot | **Закрыт**, его уникальный исторический аудит сохранён в PR #1080: `docs/ETP_EXECUTION_AUDIT_2026-11-02.md`; исправлены знаменатели CDTRF 1 408 total / 1 354 active, Mets 2 188 total / 1 216 active, Sistematorg 3 361 total / 150 active |

**Ни один PR не удалён с уничтожением ветки/истории.** Закрытие — обратимая операция. Остальные PR не объявлялись «ненужными» только потому, что их дата старая: часть содержит уникальные изменения.

## Issues — что это такое и какие из 15 следует оставить

Issue = воспроизводимая неисправность, требование или задача; открытое Issue **не означает**, что соответствующий код не существует: критерии закрытия могут требовать точную production-приёмку. PR = предлагаемые код/документы, `merge` переносит их в main. GitHub REST `/issues` возвращает в том числе PR, поэтому правильное число самостоятельных Issues в данном срезе — **15**, не 32.

| Issue | Назначение | Незакрытая проверка / решение |
|---|---|---|
| [#1084](https://github.com/Maximiliamos/torgi/issues/1084) | **P1 critical DR**, backup/restore | Run #41807377275 failed: `com.docker.service` cannot start from Home runner, verified restore не доказан; НЕ закрывать до повторного успешного backup+restore |
| [#1083](https://github.com/Maximiliamos/torgi/issues/1083) | **P1 critical Public API**, functional | Run #41807763602 failed: `live=502 ready=502 auth=502`, нужны восстановление origin и успешные current SHA health/WEB |
| [#1082](https://github.com/Maximiliamos/torgi/issues/1082) | **P1 Web**, public browser smoke | Run #41706831103: page.goto(sterdez.online/) timeout 180 sec; отличить origin 502 от frontend/network; повторить из двух сетей |
| [#1081](https://github.com/Maximiliamos/torgi/issues/1081) | **P1 Home**, health | Run #41822362340 failed: Docker Desktop Linux Engine pipe not available; без Home исправности scheduler/ingestion/map недоказуемы |
| [#1065](https://github.com/Maximiliamos/torgi/issues/1065) | **P1** Sistematorg, trade versus lot | Несмотря на sample price 93 240 ₽, production historical rows и table_lot_N требуют source acceptance, controlled migration, prices/status and map |
| [#1023](https://github.com/Maximiliamos/torgi/issues/1023) | **P1** CDTRF filter shows 0 | Доказать DB→GEO→MapDataset→S3→WEB exact SHA, 30 реальных sample, а не считать наличие DB записей успехом |
| [#1021](https://github.com/Maximiliamos/torgi/issues/1021) | **P1** единый NormalizedLot контракт | Decimal RUB, UTC+source tz, status/price provenance, distinct procedure/lot/sale, end-to-end tests всех источников; открыт обоснованно |
| [#1018](https://github.com/Maximiliamos/torgi/issues/1018) | **P1** verified ID digest from preflight to apply | Связан с уникальным draft #1019; без него возможна подмена verified lot IDs между API fetch и commit; keep open |
| [#1010](https://github.com/Maximiliamos/torgi/issues/1010) | **P1** восстановление historical region_code | Порядка 3 908 historical P13 candidates с отсутствующим region_code в old snapshot; нельзя массово заполнять только по префиксу кадастра |
| [#971](https://github.com/Maximiliamos/torgi/issues/971) | **P1** CDTRF first detail rejected | Ранний конкретный воспроизводимый 0 accepted/1 rejected; source collector менялся, но закрывать только после подтверждения реальной детали и повторного smoke |
| [#962](https://github.com/Maximiliamos/torgi/issues/962) | Umbrella P1/P2 quality/infra | Обновить ссылки на 4 закрытых superseded PR; задачи store/geo/performance остаются, нельзя закрыть по одному исправленному парсеру |
| [#931](https://github.com/Maximiliamos/torgi/issues/931) | SRC-001 Torgi Russia optional | Пауза/circuit vs live-source coverage, targeted read-only probe, отдельный source-only full import после операторского допуска |
| [#888](https://github.com/Maximiliamos/torgi/issues/888) | P2 supply chain/governance | LICENSE, signing, GitHub branch protections и off-host секреты требуют owner decisions/verification, не автоматически закрывать |
| [#887](https://github.com/Maximiliamos/torgi/issues/887) | P2 DR/off-host restore | RPO/RTO, encrypted off-host backups, Windows reboot recovery, целевой host loss drill остаются; #1084 требует немедленного исправления раньше |
| [#886](https://github.com/Maximiliamos/torgi/issues/886) | P2 oversized modules | Инкрементальный рефакторинг api/gui/scrapers/geo/WEB; не затрагивать production семантику ради косметики |

**Итог Issues:** все 15 соответствуют реальным незавершённым проверкам, требованиям владельца либо инфраструктурным ошибкам; без подтверждения их критериев ничего закрывать автоматически нельзя. Уведомления #1081–1084 имеют более высокий приоритет, чем подключение новых ЭТП.

## Состояние сервера и точного SHA

- main на старте аудита: `b474f4ecf9b40fa2bc77a6629bb08fca9e9cfdb7`; 21 direct ETП на ветке #1080 HEAD`bcb212...` в main **ещё не влиты**.
- В CI PR #1080 на предыдущем head 4ab3a77 были Production reliability и REG.RU S3 diagnostics success; эти **2 workflow** не заменяют Home P1/P11, PostgreSQL restore, 22-source acceptance.
- Backup run #41807377275: попытка запустить `com.docker.service` failed; последняя health #41822362340 не смогла открыть Docker Linux Engine. Публичный functional run #41807763602 наблюдал HTTP 502; WEB smoke #41706831103 page.goto timeout.
- Никаких новых Home DB/MapDataset/S3 чисел нельзя получать из offline unit тестов. Без отдельного надёжного Home evidence нельзя утверждать, что 09:00/21:00 scheduler исполнялся с 22 источниками или что карта после этого свежая.

## Рекомендуемый порядок приоритетов

**P1 infrastructure first**: Home Docker/PostgreSQL/restore/API public status и текущий P11 → **P1 source acceptance**: PR #1080 + #1019 identities, 21 direct full read-only + Lot-online revalidation → **P1 safe regular schedule**: #1085 and per-source accepted enrollment → bounded production import → exact source-id scoped GEO → verified S3/map/Web → **P2** dependency PRs/refactoring/GitFlic.

Изменение статуса документа только по ссылке на конкретный PR SHA + CI + датированный Home run. Старая цифра или green CI не равны принятой ЭТП.
