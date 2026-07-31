# BankrotAI implementation report

Дата актуализации: 2026-07-31. Ветка: `codex/audit-remediation`.

## Текущее состояние

BankrotAI поставляется как Windows desktop EXE и как дополнительный web/API
контур. Desktop использует SQLite и автоматически применяет Alembic-миграции;
production web-контур использует PostgreSQL, Redis, Celery и nginx.

Реализовано:

- source-scoped identity `(source_system, external_id)`;
- `CanonicalLot -> SourceLot[]` для связи карточек одного объекта;
- индексируемые поля ЭТП, процедуры, извещения, ЕФРСБ, задатка и сроков;
- история статусов, цен, GEO и AI provenance;
- immutable версии документов по SHA-256;
- официальный authenticated ЕФРСБ Publications API connector;
- connector SDK/registry и адаптеры ГИС «Торги»/TBankrot;
- калькулятор максимальной ставки в API и desktop GUI;
- чек-лист допуска к торгам;
- архивирование закрытых лотов без удаления аналитической истории;
- fail-closed API auth, Redis rate limiting, readiness и безопасный Docker;
- системное Windows TLS trust store и безопасная деградация НСПД;
- CI для Python, PostgreSQL, WEB, Docker и Playwright.

## Последняя проверка

- Ruff: passed;
- MyPy: passed;
- SQLite: 124 passed, 2 deselected;
- PostgreSQL: 2 passed;
- миграция копии пользовательской БД: 1 483 SourceLot, 1 334 CanonicalLot,
  исходные 1 483 ProcessedLot сохранены.

## Не завершено

Полный перенос реализаций из `scrapers.py`, production-доступ ЕФРСБ, S3/MinIO,
semantic document diff, роли/организации и обучающая выборка сделок остаются в
`docs/ROADMAP.md`. Корневая лицензия требует решения владельца репозитория.

Этот документ заменяет исторический отчёт «13 пунктов» от мая 2026 года.
