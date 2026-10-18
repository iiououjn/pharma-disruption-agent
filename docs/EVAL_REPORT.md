# Evaluation report (offline synthetic fixtures)

Gold pairs hand-labelled: 31

## Matching over every record x SKU pair (unlisted pairs = non_match; precision/recall ignore true negatives)

| system | TP | FP | FN | precision | recall | F1 |
|---|---|---|---|---|---|---|
| matcher | 12 | 0 | 0 | 1.0 | 1.0 | 1.0 |
| keyword_baseline | 11 | 19 | 1 | 0.367 | 0.917 | 0.524 |

Review routing: 1/1 genuinely uncertain pairs routed to review; 0 true matches and 0 non-matches sent to review.
Unlisted pairs auto-accepted by matcher (need manual check): []
Unlisted pairs hit by baseline: 0

## Metamorphic checks: 47/47 passed

## Numeric faithfulness: 28 numbers in 9 rationales, 0 unbacked, 0 recompute mismatches

## Change detection: precision 1.0 recall 1.0 (unexpected [], missed [])

## Abstention
- [PASS] missing strength -> needs_review, not prioritised 
- [PASS] no catalogue candidate -> no_match state 
- [PASS] near-miss SKUs (same ingredient, wrong strength/form/salt) are not flagged wrongly flagged: []
- [PASS] sparse source text -> missing info reported, no invented resolution date 
- [PASS] SKU with no shortage candidates is not flagged (SKU-115) 
- [PASS] missing demand -> insufficient_evidence, no priority invented 
- [PASS] ingredient-only record (no form/strength/id) is never auto-accepted 
