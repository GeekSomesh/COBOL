"""Three more business domains, used ONLY as fine-tuning training data.

``python scripts/generate.py --extra N`` writes these programs next to the main
corpus and adds them to the train split. They are kept out of ``TEMPLATES`` so
the original corpus (and its validation and test templates) regenerates byte for
byte: the held-out test set the models are compared on does not change.
"""

from __future__ import annotations

import random

from src.corpus.templates import (
    ALL, ANY, CHAIN, COMPUTE, INPUTS, L, OUTPUTS, SET, SPLIT_OF_TEMPLATE, FieldDef, Template, block, money,
    pct, rule,
)

INPUTS.update({
    "claim_amount": FieldDef("9(7)V99", ["CLAIM-AMT", "CLM-AMOUNT", "CLAIM-VALUE"], ["CL-AMT", "WS-CLAM", "C-VL"], "CLAIMED AMOUNT"),
    "policy_age_months": FieldDef("9(3)", ["POLICY-AGE-MM", "POL-MONTHS", "POLICY-TENURE"], ["PA-MM", "WS-POLM", "P-TN"], "MONTHS SINCE POLICY START"),
    "prior_claims": FieldDef("9(2)", ["PRIOR-CLAIMS", "CLAIM-COUNT", "PREV-CLM-CNT"], ["PC-CT", "WS-PCLM", "P-CC"], "CLAIMS IN LAST 3 YEARS"),
    "claim_type": FieldDef("X", ["CLAIM-TYPE", "CLM-TYPE-CD", "LOSS-TYPE"], ["CT-TY", "WS-CTYP", "L-TY"], "CLAIM TYPE A=AUTO H=HOME M=MEDICAL", {"A": "AUTO", "H": "HOME", "M": "MEDICAL"}),
    "police_report": FieldDef("X", ["POLICE-REPORT", "POL-RPT-FLG"], ["PR-FLG", "WS-PRF", "P-RP"], "POLICE REPORT FILED Y/N", {"Y": "FILED"}),
    "deductible": FieldDef("9(5)V99", ["DEDUCTIBLE-AMT", "POLICY-DEDUCT", "DEDUCT-AMT"], ["DA-AMT", "WS-DED", "P-DD"], "POLICY DEDUCTIBLE"),
    "gross_pay": FieldDef("9(7)V99", ["GROSS-PAY", "GRS-PAY-AMT", "PERIOD-GROSS"], ["GP-AMT", "WS-GRSP", "P-GR"], "GROSS PAY FOR THE PERIOD"),
    "hours_worked": FieldDef("9(3)", ["HOURS-WORKED", "HRS-WKD", "WEEK-HOURS"], ["HW-HH", "WS-HRS", "W-HR"], "HOURS WORKED THIS WEEK"),
    "employee_type": FieldDef("X", ["EMP-TYPE", "EMPLOYEE-TYPE", "EMP-CLASS"], ["ET-CD", "WS-ETYP", "E-CL"], "EMPLOYEE TYPE F=FULL-TIME P=PART-TIME C=CONTRACTOR", {"F": "FULL-TIME", "P": "PART-TIME", "C": "CONTRACTOR"}),
    "union_member": FieldDef("X", ["UNION-MEMBER", "UNION-FLG"], ["UM-FLG", "WS-UNI", "U-MB"], "UNION MEMBER Y/N", {"Y": "MEMBER"}),
    "years_service": FieldDef("9(2)", ["YRS-SERVICE", "SERVICE-YEARS", "TENURE-YRS"], ["YS-YY", "WS-SVCY", "T-YR"], "YEARS OF SERVICE"),
    "pension_opt_in": FieldDef("X", ["PENSION-OPTIN", "PENS-ENROLL-FLG"], ["PO-FLG", "WS-PENIN", "P-EN"], "PENSION ENROLMENT Y/N", {"Y": "ENROLLED"}),
    "package_weight": FieldDef("9(4)", ["PKG-WEIGHT", "PARCEL-WT", "SHIP-WEIGHT"], ["PW-KG", "WS-PWT", "S-WT"], "PACKAGE WEIGHT IN KG"),
    "distance_km": FieldDef("9(5)", ["DIST-KM", "SHIP-DISTANCE", "ROUTE-KM"], ["DK-KM", "WS-DIST", "R-KM"], "DELIVERY DISTANCE IN KM"),
    "service_level": FieldDef("X", ["SVC-LEVEL", "SERVICE-CD", "SHIP-SPEED"], ["SL-CD", "WS-SVC", "S-SP"], "SERVICE S=STANDARD E=EXPRESS O=OVERNIGHT", {"S": "STANDARD", "E": "EXPRESS", "O": "OVERNIGHT"}),
    "dest_zone": FieldDef("X(2)", ["DEST-ZONE", "ZONE-CD", "DELIV-ZONE"], ["DZ-CD", "WS-ZONE", "D-ZN"], "DESTINATION ZONE CODE"),
    "hazmat_flag": FieldDef("X", ["HAZMAT-FLAG", "DANGER-GOODS-IND"], ["HZ-FLG", "WS-HAZ", "D-GI"], "HAZARDOUS GOODS Y/N", {"Y": "HAZARDOUS"}),
    "order_value": FieldDef("9(7)V99", ["ORDER-VALUE", "ORD-TOTAL", "CART-VALUE"], ["OV-AMT", "WS-ORDV", "C-VA"], "ORDER VALUE"),
})

OUTPUTS.update({
    "claim_status": FieldDef("X", ["CLAIM-STATUS", "CLM-STAT-CD", "CLAIM-DECN"], ["CS-CD", "WS-CLST", "C-DN"], "A=APPROVE I=INVESTIGATE D=DENY", {"A": "APPROVED", "I": "INVESTIGATE", "D": "DENIED"}, "A"),
    "payout_amt": FieldDef("9(7)V99", ["PAYOUT-AMT", "CLAIM-PAYOUT", "SETTLE-AMT"], ["PO-AMT", "WS-PAY", "S-AM"], "AMOUNT PAID ON CLAIM", init=0),
    "siu_referral": FieldDef("X", ["SIU-REFERRAL", "FRAUD-UNIT-IND"], ["SU-IND", "WS-SIU", "F-UI"], "SPECIAL INVESTIGATIONS REFERRAL", {"Y": "REFERRED"}, "N"),
    "overtime_pay": FieldDef("9(5)V99", ["OVERTIME-PAY", "OT-PAY-AMT", "OT-AMOUNT"], ["OT-AMT", "WS-OTP", "O-PY"], "OVERTIME PAY", init=0),
    "union_dues": FieldDef("9(3)V99", ["UNION-DUES", "DUES-AMT"], ["UD-AMT", "WS-DUES", "U-DU"], "UNION DUES DEDUCTED", init=0),
    "pension_rate": FieldDef("9V9999", ["PENSION-RATE", "PENS-CONTRIB-RT"], ["PR-RT", "WS-PRT", "P-CR"], "PENSION CONTRIBUTION RATE", init=0),
    "benefits_flag": FieldDef("X", ["BENEFITS-ELIG", "BENEFIT-IND"], ["BE-IND", "WS-BEN", "B-EL"], "BENEFITS ELIGIBLE Y/N", {"Y": "ELIGIBLE"}, "N"),
    "ship_charge": FieldDef("9(5)V99", ["SHIP-CHARGE", "FREIGHT-AMT", "DELIV-FEE"], ["SC-AMT", "WS-SHC", "F-AM"], "SHIPPING CHARGE", init=0),
    "surcharge": FieldDef("9(4)V99", ["SURCHARGE", "EXTRA-CHG", "SURCHG-AMT"], ["SG-AMT", "WS-SUR", "X-CG"], "DELIVERY SURCHARGE", init=0),
    "free_shipping": FieldDef("X", ["FREE-SHIP", "FREE-SHIP-IND"], ["FS-IND", "WS-FSH", "F-SH"], "FREE SHIPPING Y/N", {"Y": "FREE"}, "N"),
    "ship_block": FieldDef("X", ["SHIP-BLOCK", "NO-SHIP-IND"], ["SB-IND", "WS-SBLK", "N-SH"], "SHIPMENT BLOCKED Y/N", {"Y": "BLOCKED"}, "N"),
})


# --------------------------------------------------------- insurance_claims ---

def claim_fast_track(r: random.Random) -> list[dict]:
    small = r.choice([1000, 1500, 2500])
    big = r.choice([25000, 40000, 50000])
    months = r.choice([3, 6])
    return [block("chain", ["TRIAGE-CLAIM", "2000-CLAIM-TRIAGE", "CLAIM-ROUTING"], [
        rule("Fast-track small claim",
             f"Approve claims of {money(small)} or less from customers with fewer than 2 prior claims.",
             ["claim", "amount", "prior claims", "approval"],
             ALL(L("claim_amount", "<=", small), L("prior_claims", "<", 2)), [SET("claim_status", "A")]),
        rule("Large claim investigation", f"Investigate claims above {money(big)}.",
             ["claim", "amount", "investigation"], L("claim_amount", ">", big), [SET("claim_status", "I")]),
        rule("New policy claim", f"Investigate claims on policies younger than {months} months.",
             ["claim", "policy age", "investigation"], L("policy_age_months", "<", months), [SET("claim_status", "I")]),
        rule("Standard claim", "Approve all other claims.", ["claim", "approval", "default"],
             None, [SET("claim_status", "A")]),
    ], renders=CHAIN)]


def claim_fraud_referral(r: random.Random) -> list[dict]:
    amt = r.choice([5000, 7500, 10000])
    k = r.choice([3, 4])
    return [
        block("if", ["CHECK-POLICE-RPT", "3000-AUTO-REPORT", "NO-REPORT-CHECK"], [rule(
            "Auto claim without police report",
            f"Refer auto claims above {money(amt)} without a police report to special investigations.",
            ["claim", "auto", "police report", "fraud", "referral"],
            ALL(L("claim_type", "==", "A"), L("police_report", "==", "N"), L("claim_amount", ">", amt)),
            [SET("siu_referral", "Y"), SET("claim_status", "I")])]),
        block("if", ["FREQUENT-CLAIMANT", "3100-CLAIM-FREQ", "REPEAT-CLAIMS"], [rule(
            "Frequent claimant", f"Refer customers with {k} or more prior claims to special investigations.",
            ["claim", "prior claims", "fraud", "referral"],
            L("prior_claims", ">=", k), [SET("siu_referral", "Y")])]),
    ]


def claim_payout(r: random.Random) -> list[dict]:
    share = r.choice([0.80, 0.70, 0.90])
    return [block("chain", ["CALC-PAYOUT", "4000-SETTLE-CLAIM", "PAYOUT-RULES"], [
        rule("Medical co-insurance", f"Pay {int(round(share * 100))}% of medical claims.",
             ["claim", "medical", "payout", "co-insurance"],
             L("claim_type", "==", "M"), [COMPUTE("payout_amt", f"claim_amount * {share:.2f}")]),
        rule("Deductible applies", "Pay the claimed amount minus the deductible for any other claim with an amount.",
             ["claim", "deductible", "payout"],
             L("claim_amount", ">", 0), [COMPUTE("payout_amt", "claim_amount - deductible")]),
        rule("Nothing to pay", "Deny claims with no claimed amount.", ["claim", "denial", "default"],
             None, [SET("payout_amt", 0), SET("claim_status", "D")]),
    ], renders=CHAIN)]


# ------------------------------------------------------- payroll_deductions ---

def payroll_overtime(r: random.Random) -> list[dict]:
    h1 = r.choice([50, 55, 60])
    h2 = r.choice([40, 44])
    p1, p2 = r.choice([0.25, 0.30]), r.choice([0.10, 0.15])
    return [block("chain", ["CALC-OVERTIME", "2000-OVERTIME", "OT-RULES"], [
        rule("No overtime for contractors", "Pay no overtime to contractors.",
             ["overtime", "contractor", "employee type"], L("employee_type", "==", "C"), [SET("overtime_pay", 0)]),
        rule("Heavy overtime", f"Add {int(round(p1 * 100))}% of gross pay as overtime when more than {h1} hours were worked.",
             ["overtime", "hours", "gross pay"], L("hours_worked", ">", h1),
             [COMPUTE("overtime_pay", f"gross_pay * {p1:.2f}")]),
        rule("Standard overtime", f"Add {int(round(p2 * 100))}% of gross pay as overtime when more than {h2} hours were worked.",
             ["overtime", "hours", "gross pay"], L("hours_worked", ">", h2),
             [COMPUTE("overtime_pay", f"gross_pay * {p2:.2f}")]),
        rule("No overtime", "Pay no overtime otherwise.", ["overtime", "default"], None, [SET("overtime_pay", 0)]),
    ], renders=CHAIN)]


def payroll_union_dues(r: random.Random) -> list[dict]:
    g = r.choice([2000, 3000, 4000])
    d1, d2 = r.choice([45, 55]), r.choice([25, 30])
    return [block("nested", ["UNION-DEDUCTION", "3000-UNION-DUES", "DUES-RULES"], [
        rule("Higher union dues", f"Deduct {money(d1)} union dues from members earning at least {money(g)}.",
             ["union", "dues", "gross pay", "deduction"], L("gross_pay", ">=", g), [SET("union_dues", d1)]),
        rule("Basic union dues", f"Deduct {money(d2)} union dues from other members.",
             ["union", "dues", "deduction"], None, [SET("union_dues", d2)]),
    ], renders=CHAIN, outer=L("union_member", "==", "Y"),
        outer_else=rule("Non-member", "Deduct no union dues from non-members.", ["union", "dues"], None,
                        [SET("union_dues", 0)]))]


def payroll_benefits(r: random.Random) -> list[dict]:
    h = r.choice([20, 25, 30])
    y = r.choice([2, 3, 5])
    r1, r2 = r.choice([0.0600, 0.0500]), r.choice([0.0300, 0.0200])
    return [
        block("if", ["BENEFIT-ELIG", "4000-BENEFITS", "CHECK-BENEFITS"], [rule(
            "Benefits eligibility",
            f"Make full-time employees, and part-time employees working at least {h} hours, eligible for benefits.",
            ["benefits", "eligibility", "employee type", "hours"],
            ANY(L("employee_type", "==", "F"), ALL(L("employee_type", "==", "P"), L("hours_worked", ">=", h))),
            [SET("benefits_flag", "Y")])]),
        block("if", ["PENSION-CONTRIB", "4100-PENSION", "SET-PENSION"], [rule(
            "Pension contribution rate",
            f"Contribute {pct(r1)} to the pension for enrolled employees with at least {y} years of service, "
            f"otherwise {pct(r2)}.",
            ["pension", "service years", "contribution"],
            ALL(L("pension_opt_in", "==", "Y"), L("years_service", ">=", y)),
            [SET("pension_rate", r1)], [SET("pension_rate", r2)])]),
    ]


# --------------------------------------------------------- shipping_charges ---

def ship_weight_bands(r: random.Random) -> list[dict]:
    w1, w2 = r.choice([2, 5]), r.choice([10, 20])
    c1, c2 = r.choice([5, 7]), r.choice([12, 15])
    per = r.choice([1.10, 1.25, 1.50])
    return [block("chain", ["PRICE-PARCEL", "2000-WEIGHT-BAND", "WEIGHT-PRICING"], [
        rule("Light parcel", f"Charge {money(c1)} for parcels up to {w1} kg.",
             ["shipping", "weight", "charge"], L("package_weight", "<=", w1), [SET("ship_charge", c1)]),
        rule("Medium parcel", f"Charge {money(c2)} for parcels up to {w2} kg.",
             ["shipping", "weight", "charge"], L("package_weight", "<=", w2), [SET("ship_charge", c2)]),
        rule("Heavy parcel", f"Charge {per:.2f} per kg for heavier parcels.",
             ["shipping", "weight", "charge", "default"], None,
             [COMPUTE("ship_charge", f"package_weight * {per:.2f}")]),
    ], renders=CHAIN)]


def ship_free_threshold(r: random.Random) -> list[dict]:
    v = r.choice([50, 75, 100])
    zones = sorted(r.sample(["Z7", "Z8", "Z9", "ZR"], k=2))
    s = r.choice([10, 15, 20])
    return [
        block("if", ["FREE-SHIPPING", "3000-FREE-SHIP", "CHECK-FREE-SHIP"], [rule(
            "Free standard shipping", f"Ship standard orders of at least {money(v)} for free.",
            ["shipping", "free shipping", "order value"],
            ALL(L("order_value", ">=", v), L("service_level", "==", "S")),
            [SET("free_shipping", "Y"), SET("ship_charge", 0)])]),
        block("if", ["REMOTE-ZONE", "3100-ZONE-SURCHG", "ZONE-SURCHARGE"], [rule(
            "Remote zone surcharge", f"Add a {money(s)} surcharge for deliveries to zones {', '.join(zones)}.",
            ["shipping", "zone", "surcharge"],
            L("dest_zone", "in", zones), [SET("surcharge", s)])]),
    ]


def ship_hazmat(r: random.Random) -> list[dict]:
    d = r.choice([500, 800, 1000])
    s1, s2 = r.choice([60, 75]), r.choice([25, 30])
    return [block("nested", ["HAZMAT-RULES", "4000-HAZMAT", "DANGEROUS-GOODS"], [
        rule("No overnight hazardous goods", "Block hazardous shipments sent overnight.",
             ["shipping", "hazardous", "overnight", "block"], L("service_level", "==", "O"), [SET("ship_block", "Y")]),
        rule("Long-distance hazardous surcharge", f"Add a {money(s1)} surcharge to hazardous shipments over {d} km.",
             ["shipping", "hazardous", "distance", "surcharge"], L("distance_km", ">", d), [SET("surcharge", s1)]),
        rule("Hazardous goods surcharge", f"Add a {money(s2)} surcharge to other hazardous shipments.",
             ["shipping", "hazardous", "surcharge"], None, [SET("surcharge", s2)]),
    ], renders=CHAIN, outer=L("hazmat_flag", "==", "Y"))]


EXTRA_TEMPLATES: list[Template] = [
    Template("claim_fast_track", "insurance_claims", ["claim_amount", "prior_claims", "policy_age_months"], ["claim_status"], claim_fast_track),
    Template("claim_fraud_referral", "insurance_claims", ["claim_type", "police_report", "claim_amount", "prior_claims"], ["siu_referral", "claim_status"], claim_fraud_referral),
    Template("claim_payout", "insurance_claims", ["claim_type", "claim_amount", "deductible"], ["payout_amt", "claim_status"], claim_payout),
    Template("payroll_overtime", "payroll_deductions", ["employee_type", "hours_worked", "gross_pay"], ["overtime_pay"], payroll_overtime),
    Template("payroll_union_dues", "payroll_deductions", ["union_member", "gross_pay"], ["union_dues"], payroll_union_dues),
    Template("payroll_benefits", "payroll_deductions", ["employee_type", "hours_worked", "pension_opt_in", "years_service"], ["benefits_flag", "pension_rate"], payroll_benefits),
    Template("ship_weight_bands", "shipping_charges", ["package_weight"], ["ship_charge"], ship_weight_bands),
    Template("ship_free_threshold", "shipping_charges", ["order_value", "service_level", "dest_zone"], ["free_shipping", "ship_charge", "surcharge"], ship_free_threshold),
    Template("ship_hazmat", "shipping_charges", ["hazmat_flag", "service_level", "distance_km"], ["ship_block", "surcharge"], ship_hazmat),
]
EXTRA_PREFIX = {"insurance_claims": "CLM", "payroll_deductions": "PAY", "shipping_charges": "SHP"}
SPLIT_OF_TEMPLATE.update({t.id: "train" for t in EXTRA_TEMPLATES})   # training data only
