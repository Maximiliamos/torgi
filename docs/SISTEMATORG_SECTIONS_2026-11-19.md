# Sistematorg numbered-section boundaries, 19 November 2026

The public procedure response repeats «Лот №1» in its .lot_header/.lot_title,
its asset-name cell and its description cell. Flattened-text marker discovery
mistook those mentions for separate numbered sections and rejected valid lots.

Use the actual .lot_header containing .lot_title as modern section boundaries,
and require the independently printed «Номер лота» inside each resulting
section to agree. Non-lot headers such as printable notices are not boundaries.
Legacy pages without these structural headers retain their conservative text
contract. Actual repeated section IDs, missing/mismatched number fields and
inconsistent declared lot counts still fail. Internal delimiters never enter
stored descriptions.

Read-only checks of unchanged captures:
- procedure 66404: one independently numbered lot, initial price 609840;
- procedure 65750: two separately numbered lots, initial prices 3505500/617400.

98 source/contract tests passed, including repeated text mentions, a real
repeated section ID, a mismatched independent lot-number field and a missing
number field. The full existing database audit and migration of legacy
procedure-only identities are separate and remain open. No source records,
user grades, archive state or coordinates are modified by this parser patch.
