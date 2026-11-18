# CenterR UTF-8 content decoding, 19 November 2026

A full read-only check of 70 existing CenterR cards left one rejected record:
provider lot 1171405. The unchanged public response contained a valid own
procedure 0101344, lot 1 and property 74:12:1408001:41, with initial price
88830. BeautifulSoup's byte-encoding guess decoded the UTF-8 Russian text
as another encoding and hid the identity labels from the parser.

Decode valid UTF-8/UTF-8-BOM bytes before HTML parsing in catalogue, pagination
and detail helpers. Non-UTF-8 legacy documents retain the previous encoding
path. Identity, isolated-field, catalogue-collision and pagination checks are
unchanged; no price/status default or identity exception was introduced.

94 CenterR/ETP tests passed. New regressions cover UTF-8 with a misleading
legacy charset, UTF-8 BOM and actual Windows-1251 content. Re-parsing the
unchanged failing capture recovers the exact lot and price without another
HTTP request. Production rollout and source-data repair remain separate.
