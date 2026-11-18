# Retrospective address precision repair

New geocoding already rejects settlement centroids, wrong house numbers,
wrong building units and wrong streets. Previously accepted address points
also need the same revalidation. The bounded GEO policy operator now checks
the retained current Photon/Nominatim snapshot using that shared predicate.
Exact cadastral points remain governed by their own source identity checks.

Preview and commit cover the processed row, exact bound source row, retained
snapshot and current GEO failure record. A concurrent change invalidates the
preview. The audit records the snapshot state digest in addition to its ID
and rejection reason. Source facts, user grades, notes and history remain;
only the unproven current point is reset and eligible rows are queued again.

New frozen NSPD associations can also pin the precise current source field
and its verified SHA/time receipt. A title-only confirmation must literally
contain that row's own cadastral number and match the stored verified title
value. Changed, expired, duplicate or foreign confirmations reject the
association before persistence. This associates primary geometry with an
owned object; it does not transfer auction facts between publications.

This change does not alter map coverage thresholds or pipeline revision.
Fresh primary cadastral replacements should be applied first. Remaining
imprecise points require a frozen cohort, backup, preview, bounded trial and
commit, independent whole-cohort verification and a separately reviewed map
transition. Census candidates are not claimed to have been repaired here.
