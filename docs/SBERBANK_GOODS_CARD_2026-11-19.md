# Sberbank-AST GoodsCard v2: primary detail contract

The current GoodsView HTML is a Vue template. Its headings are Help/Contacts;
navigation/footer property labels do not prove individual-lot content. Two
public captures for goods 62043 and 61616 reproduced the old parser returning
Contacts, no price, and unknown status despite concrete catalogue values.

The first-party GoodsViewCatalog.Contract.js, Apl.js, aplFormScript.js and
CoreApl/core.js describe the actual form. Public /build/apl_config.json pins
the API base to https://utp.sberbank-ast.ru/api/. The read-only request uses
Processing/main with actionType TEMPLATE and both windowCode/actionCode set
to the exact GoodsView path. No login/action/submission endpoint is used.

The response has GoodsCard.ContractVersion=2. Accept only the own Goods.Type.Id
and Goods.Id matching the requested type/0/0/id tuple. All own Goods.Source,
Bid and Purchase sections must be bankruptcy section 13, and the source's
Purchase.Id, when present, must match the own Purchase.Id. V2 also has valid
cards without that redundant reference; own Bid.Id and Purchase.Id remain
mandatory and their relation is scoped to the exact root GoodsCard. Other middle path components and
schema versions fail closed. Recommendations and RelatedBids cannot supply
the selected lot's title, cadastral number, status or price.

Use own Goods.Name/Description, own Location.Address (or own Goods.Region),
and own Bid.Price.Initial/Current, Bid.Status.Name and Bid.Deposit. Goods.Price
is an advertisement value and cannot replace a missing own Bid.Price. No
organizer/contact address or Location coordinate is accepted as a precise
property location. Explicit operation type is retained for sale eligibility.

Unzoned Bid.Schedule ISO values remain raw. Their presence does not prove
Europe/Moscow; this patch does not create auction dates or a timezone claim.
Missing own fields remain missing and unknown status remains unknown.

Validation: 114 contract, connector and ETP tests passed, including exact
identity/scope/purchase failures, recommendations, shell rejection, missing
own fields, read-only POST shape and lease exclusion. Whole-database source
repair, production deployment, two three-source passes and final pipeline
acceptance are separate operations, not certified by these tests.

The existing-cohort audit checked all 114 source identities. Initial prices
were present for 114 and current prices for 113; 18 native status labels stay
unknown in the canonical status mapping. This is field-scoped evidence,
not a claim that every field is verified.

Four industrial-line titles contained «линия концевого участка»: the shared
classifier had mistaken the industrial section for a land plot. Recognize
an industrial line as equipment when it precedes a property noun, preserving
buildings sold with equipment. Remove the generic «участ» substring from
fallback property terms so participation boilerplate cannot prove land;
a primary «Участок № ...» title still qualifies. These checks also reject
an industrial line carrying a stale property category in an older record.

Expanded validation: 160 source/classification/filter/map tests passed.

A current source page can legitimately contain no property after filtering
while the same proven catalogue has a next page. The read-only checklist
now advances across that specific verified Sberbank condition, keeps empty
unproven pages failing, and does not certify a terminal page from a count.
174 source/classification/filter/map/checklist tests passed together.
