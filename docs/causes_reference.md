# Cause reference

Each non-perfect row gets one **probable cause**: the matching rule with the lowest priority number.
All rules that matched are listed in the column *All cause codes*. The logic is in
`src/pricing_reconciler/causes.py`; wording, confidence and actions are in `config/causes.yaml`.

These are **probable** causes derived from the resolved condition records. They point the analyst to
where to look; they are not a proof.

| Prio | Rule | Code | Fires when | Confidence |
|---:|---|---|---|---|
| 10 | R01 | NO_KEY_MAPPING | the customer or material has no row in the mapping tables | High |
| 20 | R02 | CURRENCY_MISMATCH | the record found on both sides has a different currency | High |
| 30 | R03 | PRICING_UNIT_MISMATCH | pricing unit (KPEIN) or unit of measure (KMEIN) differs | High |
| 40 | R07 | VALIDITY_DATE_GAP | S/4HANA has the record, but it is not valid on the pricing date | High |
| 50 | R04 | MISSING_RECORD_S4 | ECC resolves a record, S/4HANA resolves none | High |
| 60 | R06 | ACCESS_LEVEL_DIFFERENCE | both resolve a record, but S/4HANA uses a less specific key level | Medium |
| 70 | R08 | SCALE_DIFFERENCE | same record, different scale tier at the reference quantity | Medium |
| 80 | R05 | EXTRA_RECORD_S4 | S/4HANA resolves a record, ECC resolves none | Medium |
| 90 | R09 | AMOUNT_CHANGED | same level and dates, different amount | Medium |
| 100 | R10 | ROUNDING | every step differs by at most the tolerance (0.01) | Medium |
| 999 | R99 | UNEXPLAINED | nothing above explains it: manual review | Low |

Order matters: structural causes (keys, currency, units, dates) come before value causes, because
a structural problem usually also changes the amounts.
