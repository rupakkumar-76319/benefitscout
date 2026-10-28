# BenefitScout evals: does the Nano / Super / Ultra split earn its keep?

8 labelled profiles across India, the US, the UK, Germany and Canada, 21 expected (scheme, verdict) pairs. Profile, plan and triage run on the default models in every configuration and Tavily results are cached, so every configuration starts from the same web pages; the triage, rule-extraction and judging models change. Labels are in `evals/profiles.json`; rerun with `python evals/run_evals.py`.

| Configuration | Triage by | Rules by | Verdicts by | Expected schemes found | Verdict accuracy on found | Correct / expected | Nemotron cost (8 runs) | of which judge | of which extract |
|---|---|---|---|---|---|---|---|---|---|
| split | Nemotron 3 Super 120B | Nemotron 3 Super 120B | Nemotron 3 Ultra 550B | 94% | 88% | 18/21 | $0.608 | $0.441 | $0.115 |
| judge_super | Nemotron 3 Super 120B | Nemotron 3 Super 120B | Nemotron 3 Super 120B | 94% | 62% | 14/21 | $0.270 | $0.106 | $0.115 |
| judge_nano | Nemotron 3 Super 120B | Nemotron 3 Super 120B | Nemotron 3 Nano 30B | 94% | 75% | 16/21 | $0.200 | $0.036 | $0.115 |
| triage_nano | Nemotron 3 Nano 30B | Nemotron 3 Super 120B | Nemotron 3 Ultra 550B | 75% | 92% | 16/21 | $0.587 | $0.436 | $0.130 |

Accuracy counts a verdict as correct when it is in the allowed set for that scheme (for example a UK Carer's Allowance claim may be `likely` or `need_info`, because it depends on the parent's benefit, which the person did not state).

## Failure cases

### split (3 failures)
- **up_obc_entrepreneur_hindi** - Centrally Sponsored Scheme of Post-Matric Scholarship for OBC, EBC and DNT Students for studying in India: got `likely`, expected one of `not_eligible` (not currently studying, so a student scholarship should be skipped or rejected). Model said: "You meet the citizenship, residence, category, education, and income criteria for the OBC post-matric scholarship. The only uncertainty is the family-boy limit, which depends on your gender and your brothers' scholarship history."
- **germany_student** - expected `deutschlandstipendium` was never surfaced (merit based; needs grades).
- **karnataka_farmer** - Pradhan Mantri Kisan Samman Nidhi (PM-KISAN): got `need_info`, expected one of `eligible/likely` (landholding farmer family). Model said: "You appear to be a small farmer with cultivable land, but the scheme requires the land to be in your name per land records as of 1 Feb 2019, and your family must match the definition (husband, wife, minor children who own the land). We need to confirm these details."

### judge_super (7 failures)
- **bihar_sc_student** - Bihar Post Matric Scholarship SC-ST: got `need_info`, expected one of `eligible/likely` (SC, Bihar domicile, income under 2.5 lakh, post-matric course). Model said: "You meet the SC category and residency requirements, but we need to verify your academic document dates and any other scholarship to determine eligibility."
- **uk_carer** - Universal Credit: got `eligible`, expected one of `likely/need_info` (low income, working age; savings threshold unknown). Model said: "Because you are out of work, you satisfy the core eligibility rule for Universal Credit, which can help with living costs, caregiving, and housing."
- **up_obc_entrepreneur_hindi** - Pradhan Mantri Mudra Yojana (PMMY): got `need_info`, expected one of `eligible/likely` (non-farm micro enterprise loan, any adult). Model said: "You satisfy the age, citizenship, and business‑type criteria for a Mudra loan. Eligibility hinges on knowing the loan size you need and whether you have a business plan."
- **up_obc_entrepreneur_hindi** - Centrally Sponsored Scheme of Post-Matric Scholarship for OBC, EBC and DNT Students for studying in India: got `need_info`, expected one of `not_eligible` (not currently studying, so a student scholarship should be skipped or rejected). Model said: "You satisfy citizenship, residence, category, education, and income conditions. Eligibility hinges only on whether your family has more than two boys, which is unknown."
- **germany_student** - expected `deutschlandstipendium` was never surfaced (merit based; needs grades).
- **karnataka_farmer** - Pradhan Mantri Kisan Samman Nidhi (PM-KISAN): got `need_info`, expected one of `eligible/likely` (landholding farmer family). Model said: "You meet all the known eligibility requirements for PM‑KISAN, but we need to confirm whether your landholding existed before the 01‑Feb‑2019 cutoff (or came via succession) to finalize eligibility."
- **karnataka_farmer** - Pradhan Mantri Fasal Bima Yojana: got `need_info`, expected one of `eligible/likely` (cultivating farmer with KCC). Model said: "If your crops and area are notified under the scheme, you would be eligible for crop insurance; otherwise you would not qualify."

### judge_nano (5 failures)
- **ohio_single_mother** - Ohio Women, Infants, and Children (WIC) Program: got `not_eligible`, expected one of `eligible/likely/need_info` (4-year-old qualifies; income under 185% FPL). Model said: "You are not pregnant or postpartum, so you do not meet the WIC category, even though your income and residence qualify."
- **uk_carer** - Universal Credit: got `eligible`, expected one of `likely/need_info` (low income, working age; savings threshold unknown). Model said: "You meet the core eligibility condition for Universal Credit as you are out of work and likely on a low income."
- **up_obc_entrepreneur_hindi** - Centrally Sponsored Scheme of Post-Matric Scholarship for OBC, EBC and DNT Students for studying in India: got `likely`, expected one of `not_eligible` (not currently studying, so a student scholarship should be skipped or rejected). Model said: "You are an OBC male student from Uttar Pradesh with a family income below the threshold, but the number of male siblings and your gender are not known, which could affect eligibility."
- **germany_student** - expected `deutschlandstipendium` was never surfaced (merit based; needs grades).
- **us_veteran** - Post-9/11 GI Bill (Chapter 33): got `need_info`, expected one of `eligible/likely` (12 years active duty after 9/11, honorable discharge). Model said: "You may qualify for the Post-9/11 GI Bill if you served on active duty after September 10 2001; we need to confirm that service period."

### triage_nano (5 failures)
- **bihar_sc_student** - expected `post.?matric scholarship.*(sc|scheduled caste)` was never surfaced (SC, Bihar domicile, income under 2.5 lakh, post-matric course).
- **uk_carer** - expected `universal credit` was never surfaced (low income, working age; savings threshold unknown).
- **germany_student** - expected `deutschlandstipendium` was never surfaced (merit based; needs grades).
- **karnataka_farmer** - Pradhan Mantri Kisan Samman Nidhi (PM-KISAN): got `need_info`, expected one of `eligible/likely` (landholding farmer family). Model said: "You appear to be a small farmer with cultivable land, but the scheme requires the land to be in your name per land records as of 1 Feb 2019, and your family must match the definition (husband, wife, minor children who own the land). We need to confirm these details."
- **canada_senior** - expected `old age security|oas` was never surfaced (65+, 10+ years residence).
