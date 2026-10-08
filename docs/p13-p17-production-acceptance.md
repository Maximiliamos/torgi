# P13–P17: обязательная производственная приёмка

> **UNACCEPTED / STOP-SHIP**, пока эти условия не доказаны на точном production SHA.
> Зелёный unit CI не означает, что настоящие данные очищены.

## Перед публикацией

1. Подтвердить текущий `main`, один Home/REG.RU/Cloudflare SHA,
   версии текущего MapDataset и прошедшие P1/P11 на этом SHA.
2. Сохранить и проверить PostgreSQL backup на `D:\\BankrotAI\\dr-backups`:
   восстановление в изолированном PostgreSQL 17, checksum, schema/version
   и минимум три верифицированные поколения. **Не удалять исторические лоты.**
3. Из артефакта `p16-home-readonly-baseline` зафиксировать причины загрязнения
   и совокупные значения. Счётчики пересекаются — не суммировать их.
4. Из `p13-live-public-map-impact` (PR #936) получить *exact proposed SQL*
   `proposed_maximum_eligible_points`, текущие `point_count`,
   `upper_bound_ratio_to_current_dataset`, разрезы source/region/category.
   Это верхняя граница новых точек; spatial validation ещё может отсеять лоты.
   При `preview_fails_existing_coverage_guard=true` — **STOP** и исправлять
   источники/статусы/GEO, не снижать `MIN_MAP_COVERAGE_RATIO`.
5. Подтвердить effective active/paused/circuit состояние четырёх источников.
   TBankrot **не включать**. `torgi-russia.ru` считается проверенным только
   при полноценном успешном sync + свежих SourceLot, а не по наличию кода.
6. Проверить что кандидаты с незнакомым статусом, плохим GEO и аренда
   исключены, но свежие продажи не потеряны. Для неоднозначных лотов —
   ручная разборка и bounded revalidation, не массовая архивация.
7. Проводить исправления только отдельными партиями при прошедшей
   проверке checksum+isolated restore, с `archive_reason` и историей
   `LotStatusEvent/LotStatusHistory`. Каждую партию проверять read-only
   и оставлять rollback. Периодические источники не активировать вслепую.

## Пять обязательных известных случаев

| Идентификатор | Обязательный результат |
|---|---|
| `76:23:060521:48` | Аренда помещения на Щепкина, 8; не публиковать |
| VIN `LWJPCBL24RB000657` | Мопед; не публиковать |
| `76:22:010717:536` | Аренда земли; не публиковать |
| `76:09:082601:3891` | Завершённые торги; не публиковать |
| `76:02:022201:38` | Не принимать адресный/районный centroid без подтверждения cadastral ID |

По каждому — доказать путь `SourceLot -> CanonicalLot -> ProcessedLot ->
LotGeoSnapshot -> MapDataset -> S3 -> public browser`. Одних in-memory тестов
или отсутствия метки в старом клиентском кеше недостаточно. Документировать
реальную `source_status`, freshness, `archive_reason` и source GEO, без
публикации персональных данных.

## Контрольная случайная выборка

- 50 земельных участков.
- 30 помещений.
- 20 домов/квартир.

Использовать зафиксированный seed `20261008` из
`scripts/p16-public-map-dry-run.py` и реально проверить снимок выборки после
раскатки новой карты: source status, вид сделки (продажа/аренда), категория,
точная геометрия, MapDataset и отображение на публичном сайте.
Если категория содержит меньше нужного числа, указать фактическую выборку,
а не дополнять повторениями.

## Критерии выхода / FAIL-CLOSED

- `public_rental_count = 0`, `public_transport_count = 0`,
  `public_closed_count = 0`, `public_movable_count = 0`,
  `stale_source_only_count = 0`,
  `cadastral_address_fallback_count = 0` в новом MapDataset.
- `low_quality_geo_count` и `locality_mismatch_count` объяснены и нулевые
  для всех проверяемых точных кадастровых объектов. Незнакомый статус = no map.
- Публикация не проходит при нарушении покрытия по источнику/региону.
  Один current/ready MapDataset, проверенный прежний rollback dataset.
- WEB/REG.RU/Home/Cloudflare подняты на точном одинаковом SHA
  (push, dispatch, rerun, recovery). P1/P11/WEB smoke и реальное подключение
  без VPN прошли, невыполненные проверки ≠ PASS.
- Только после этого обновить `docs/ROADMAP.md` на «P13–P17 accepted»,
  релиз, release evidence и закрыть дефекты.

## Что доказано на момент составления (2026-10-08)

P16 реальный read-only run `37762789583` = SUCCESS, 46 517 первичных
неархивных объектов с координатами и многочисленные неоднозначные старые
проекции. P1 ранее сообщал 45 337 точек в текущем MapDataset. Эти числа
несопоставимы напрямую из-за разного времени и условий SQL.

Основной P13–P17 PR #932 прошёл Python/PG/WEB/Windows/S3 CI на SHA
`7d0dacc9`, **но не прошёл production rollout/полную приёмку**. Его code
green не должен использоваться для подтверждения 100%.
