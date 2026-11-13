# Meta Campaign Objectives + Optimization Goals (v21+ ODAX)

Quick reference for valid `objective` + `optimization_goal` + `billing_event` combinations under the ODAX (outcome-driven) framework.

## Objectives

| `objective` | Use case |
|---|---|
| `OUTCOME_AWARENESS` | Brand reach, video views |
| `OUTCOME_TRAFFIC` | Drive clicks to website/app |
| `OUTCOME_ENGAGEMENT` | Post engagement, page likes, messages |
| `OUTCOME_LEADS` | Lead form fills, website conversions |
| `OUTCOME_APP_PROMOTION` | App installs / app events |
| `OUTCOME_SALES` | Purchases (catalog + standard conversion) |

## Common ad set configs

### Lead gen → website conversion (the most common lead-gen setup)
```python
objective = "OUTCOME_LEADS"
optimization_goal = "OFFSITE_CONVERSIONS"
billing_event = "IMPRESSIONS"
destination_type = "WEBSITE"
promoted_object = {"pixel_id": "...", "custom_event_type": "LEAD", "page_id": "..."}
```

### Lead gen → on-Meta lead form (no pixel needed)
```python
objective = "OUTCOME_LEADS"
optimization_goal = "LEAD_GENERATION"
billing_event = "IMPRESSIONS"
destination_type = "ON_AD"
promoted_object = {"page_id": "..."}  # + lead form must be attached at ad level
```

### Traffic → website clicks
```python
objective = "OUTCOME_TRAFFIC"
optimization_goal = "LINK_CLICKS"
billing_event = "IMPRESSIONS"  # or LINK_CLICKS for CPC
destination_type = "WEBSITE"
# no promoted_object needed
```

### Awareness → reach
```python
objective = "OUTCOME_AWARENESS"
optimization_goal = "REACH"
billing_event = "IMPRESSIONS"
# frequency_control_specs typically set here
```

### Engagement → page likes (legacy but still works)
```python
objective = "OUTCOME_ENGAGEMENT"
optimization_goal = "PAGE_LIKES"
billing_event = "IMPRESSIONS"
promoted_object = {"page_id": "..."}
```

## `custom_event_type` values
Standard events Meta optimizes on (must be firing on the pixel for ML to work):
- `LEAD` — most common for lead gen
- `COMPLETE_REGISTRATION`
- `PURCHASE`
- `INITIATE_CHECKOUT`
- `ADD_TO_CART`
- `VIEW_CONTENT` (very high frequency — usually not for optimization)
- `SUBSCRIBE`
- `START_TRIAL`
- `SCHEDULE`
- `CONTACT`
- `SUBMIT_APPLICATION`
- `OTHER` (with a custom conversion ID)

## Bid strategies
- `LOWEST_COST_WITHOUT_CAP` — default, max delivery
- `LOWEST_COST_WITH_BID_CAP` — set `bid_amount` to cap CPM
- `COST_CAP` — target cost per result, set `bid_amount`
- `LOWEST_COST_WITH_MIN_ROAS` — for sales objective, set `roas_average_floor`

## Special ad categories
Set `special_ad_categories` on the campaign for regulated verticals. Restricts targeting (no age/zip/gender exclusion, limited interest targeting):
- `HOUSING`
- `EMPLOYMENT`
- `CREDIT`
- `ISSUES_ELECTIONS_POLITICS`
- `ONLINE_GAMBLING_AND_GAMING`

Home services, for example, need **none**. If you are unsure whether your offer falls into one, check Meta's policy before launching — the category cannot be changed on a live campaign.
