"""Domain templates for the synthetic corpus (docs/dataset.md, implementation.md 3.3).

Each template samples one program's decision logic. Conditions and actions use
business field names; :mod:`src.corpus.spec` picks the COBOL names. Every rule
carries a ``title``, ``intent`` and ``concepts`` so gold rules can supervise the
LLM enrichment stage.

Block structures:
  if      each rule is its own top-level IF (optional ``else``)
  chain   ordered, first-match rules plus optional default (``when: None``);
          rendered as ELSE-IF chain, EVALUATE TRUE or EVALUATE <subject>
  nested  an ``outer`` condition wrapping a chain, with optional ``outer_else``
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# ------------------------------------------------------------------ fields ---


@dataclass
class FieldDef:
    pic: str
    clean: list[str]
    cryptic: list[str]
    comment: str
    labels: dict[str, str] = field(default_factory=dict)   # value -> label (88-level names)
    init: Any = None                                       # outputs only

    @property
    def numeric(self) -> bool:
        return not self.pic.startswith("X")


INPUTS: dict[str, FieldDef] = {
    "customer_age": FieldDef("9(3)", ["CUST-AGE", "CUSTOMER-AGE", "CLNT-AGE"], ["CA-AGE", "WS-CAGE", "C-AG-YY"], "CUSTOMER AGE IN YEARS"),
    "transaction_amount": FieldDef("9(7)V99", ["TXN-AMOUNT", "TRAN-AMT", "TXN-AMT"], ["TA-AMT", "T-AMT1", "WS-TXAMT"], "TRANSACTION AMOUNT"),
    "country_code": FieldDef("X(2)", ["CNTRY-CODE", "COUNTRY-CD", "ORIG-CTRY"], ["CC-CD", "WS-CTRY", "O-CT"], "ORIGIN COUNTRY CODE"),
    "txn_count_24h": FieldDef("9(3)", ["TXN-COUNT-24H", "TRAN-CNT-DAY", "DAILY-TXN-CNT"], ["TC-24", "WS-TCNT", "D-TC"], "TRANSACTIONS IN LAST 24 HOURS"),
    "account_age_days": FieldDef("9(5)", ["ACCT-AGE-DAYS", "ACCT-OPEN-DAYS"], ["AAD-DYS", "WS-AGEDD", "A-OD"], "DAYS SINCE ACCOUNT OPENED"),
    "channel_code": FieldDef("X", ["CHANNEL-CODE", "CHNL-CD", "TXN-CHANNEL"], ["CH-CD", "WS-CHN", "T-CH"], "CHANNEL O=ONLINE B=BRANCH A=ATM", {"O": "ONLINE", "B": "BRANCH", "A": "ATM"}),
    "card_present": FieldDef("X", ["CARD-PRESENT", "CARD-PRES-FLG"], ["CP-FLG", "WS-CPF", "C-PR"], "CARD PRESENT Y/N", {"Y": "PRESENT", "N": "NOT-PRESENT"}),
    "account_type": FieldDef("X", ["ACCT-TYPE", "ACCOUNT-TYPE", "ACCT-TYP"], ["AT-CD", "WS-ATYP", "A-TY"], "ACCOUNT TYPE S=SAVINGS C=CHECKING P=PREMIUM G=GOLD B=BUSINESS F=FIXED", {"S": "SAVINGS", "C": "CHECKING", "P": "PREMIUM", "G": "GOLD", "B": "BUSINESS", "F": "FIXED"}),
    "balance": FieldDef("9(9)V99", ["ACCT-BALANCE", "CURR-BAL", "LEDGER-BAL"], ["CB-AMT", "WS-BAL", "L-BL"], "CURRENT LEDGER BALANCE"),
    "term_months": FieldDef("9(3)", ["TERM-MONTHS", "DEP-TERM-MM", "DEPOSIT-TERM"], ["TM-MM", "WS-TRM", "D-TM"], "DEPOSIT TERM IN MONTHS"),
    "base_rate": FieldDef("9V9999", ["BASE-RATE", "BASE-INT-RATE", "STD-RATE"], ["BR-RT", "WS-BRT", "S-RT"], "BASE INTEREST RATE"),
    "avg_balance": FieldDef("9(7)V99", ["AVG-BALANCE", "AVG-MTH-BAL", "MEAN-BAL"], ["AB-AMT", "WS-AVBL", "M-BL"], "AVERAGE MONTHLY BALANCE"),
    "student_flag": FieldDef("X", ["STUDENT-FLAG", "STUDENT-IND"], ["SF-IND", "WS-STU", "S-IN"], "STUDENT INDICATOR Y/N", {"Y": "STUDENT"}),
    "txn_count_month": FieldDef("9(3)", ["TXN-COUNT-MTH", "MTH-TRAN-CNT", "MONTHLY-TXNS"], ["TCM-CT", "WS-MTC", "M-TX"], "TRANSACTIONS THIS MONTH"),
    "credit_score": FieldDef("9(3)", ["CREDIT-SCORE", "CR-SCORE", "BUREAU-SCORE"], ["CS-SCR", "WS-CSCR", "B-SC"], "BUREAU CREDIT SCORE"),
    "annual_income": FieldDef("9(7)V99", ["ANNUAL-INCOME", "ANN-INCOME", "GROSS-INCOME"], ["AI-AMT", "WS-ANINC", "G-INC"], "GROSS ANNUAL INCOME"),
    "debt_ratio": FieldDef("9V99", ["DEBT-RATIO", "DTI-RATIO", "DEBT-INC-RATIO"], ["DR-RT", "WS-DTI", "D-IR"], "DEBT TO INCOME RATIO"),
    "bankruptcy_flag": FieldDef("X", ["BANKRUPTCY-FLAG", "BKRPT-IND", "PRIOR-BK-FLG"], ["BK-FLG", "WS-BKF", "P-BK"], "PRIOR BANKRUPTCY Y/N", {"Y": "BANKRUPT"}),
    "years_since_bk": FieldDef("9(2)", ["YRS-SINCE-BK", "BK-YEARS"], ["YB-YY", "WS-BKY", "B-YR"], "YEARS SINCE BANKRUPTCY DISCHARGE"),
    "employment_years": FieldDef("9(2)", ["EMPLOY-YEARS", "YRS-EMPLOYED", "JOB-TENURE"], ["EY-YY", "WS-EMPY", "J-TN"], "YEARS WITH CURRENT EMPLOYER"),
    "ltv_pct": FieldDef("9(3)", ["LTV-PCT", "LOAN-TO-VALUE", "LTV-RATIO"], ["LV-PC", "WS-LTV", "L-TV"], "LOAN TO VALUE PERCENT"),
    "days_late": FieldDef("9(3)", ["DAYS-LATE", "DAYS-PAST-DUE", "DPD-DAYS"], ["DL-DD", "WS-DLT", "D-PD"], "DAYS PAYMENT IS LATE"),
    "balance_due": FieldDef("9(7)V99", ["BALANCE-DUE", "AMT-DUE", "STMT-DUE-AMT"], ["BD-AMT", "WS-BDUE", "S-DU"], "STATEMENT AMOUNT DUE"),
    "late_count_12m": FieldDef("9(2)", ["LATE-CNT-12M", "LATE-PMTS-YR"], ["LC-12", "WS-LCNT", "L-PY"], "LATE PAYMENTS IN LAST 12 MONTHS"),
    "first_offense": FieldDef("X", ["FIRST-OFFENSE", "FIRST-LATE-IND"], ["FO-IND", "WS-FOF", "F-LI"], "FIRST LATE PAYMENT Y/N", {"Y": "FIRST-TIME"}),
    "customer_tier": FieldDef("X", ["CUST-TIER", "CUSTOMER-TIER", "LOYALTY-TIER"], ["CT-CD", "WS-TIER", "L-TR"], "TIER G=GOLD P=PLATINUM S=SILVER B=BASIC", {"G": "GOLD", "P": "PLATINUM", "S": "SILVER", "B": "BASIC"}),
}

OUTPUTS: dict[str, FieldDef] = {
    "review_flag": FieldDef("X", ["FLAG-REVIEW", "REVIEW-FLAG", "RVW-IND"], ["FR-FLG", "WS-RVF", "R-IND"], "MANUAL REVIEW FLAG", {"Y": "REVIEW-NEEDED"}, "N"),
    "hold_flag": FieldDef("X", ["HOLD-FLAG", "TXN-HOLD-IND"], ["HF-FLG", "WS-HLD", "H-IN"], "HOLD TRANSACTION FLAG", {"Y": "ON-HOLD"}, "N"),
    "risk_level": FieldDef("X", ["RISK-LEVEL", "RISK-LVL", "RISK-GRADE"], ["RL-CD", "WS-RSK", "R-GR"], "RISK LEVEL H/M/L", {"H": "HIGH", "M": "MEDIUM", "L": "LOW"}, "L"),
    "risk_score": FieldDef("9(3)", ["RISK-SCORE", "FRAUD-SCORE"], ["RS-SC", "WS-FSC", "F-SC"], "FRAUD RISK SCORE", init=0),
    "interest_rate": FieldDef("9V9999", ["INT-RATE", "INTEREST-RATE", "APPLIED-RATE"], ["IR-RT", "WS-IRT", "I-RT1"], "APPLIED INTEREST RATE", init=0),
    "bonus_rate": FieldDef("9V9999", ["BONUS-RATE", "FINAL-RATE", "EFFECTIVE-RATE"], ["BN-RT", "WS-BNR", "E-RT"], "RATE AFTER BONUS", init=0),
    "monthly_fee": FieldDef("9(3)V99", ["MONTHLY-FEE", "MTH-FEE", "SVC-CHARGE"], ["MF-AMT", "WS-MFEE", "S-CH"], "MONTHLY SERVICE FEE", init=0),
    "fee_waived": FieldDef("X", ["FEE-WAIVED", "WAIVE-IND", "FEE-WAIVER-FLG"], ["FW-IND", "WS-FWV", "W-IN"], "FEE WAIVER INDICATOR", {"Y": "WAIVED"}, "N"),
    "excess_fee": FieldDef("9(5)V99", ["EXCESS-FEE", "OVERLIMIT-FEE", "EXTRA-TXN-FEE"], ["EF-AMT", "WS-XFEE", "X-FE"], "EXCESS TRANSACTION FEE", init=0),
    "decision": FieldDef("X", ["DECISION-CD", "APPL-DECISION", "CREDIT-DECN"], ["DC-CD", "WS-DEC", "A-DC"], "A=APPROVE R=REFER D=DECLINE", {"A": "APPROVED", "R": "REFERRED", "D": "DECLINED"}, "D"),
    "reason_code": FieldDef("X(3)", ["REASON-CODE", "DECLINE-RSN", "DECN-REASON"], ["RC-CD", "WS-RSN", "D-RS"], "DECISION REASON CODE", init=" "),
    "credit_limit": FieldDef("9(7)V99", ["CREDIT-LIMIT", "APPROVED-LIMIT", "CARD-LIMIT"], ["CL-AMT", "WS-CLIM", "C-LM"], "ASSIGNED CREDIT LIMIT", init=0),
    "max_loan": FieldDef("9(9)V99", ["MAX-LOAN-AMT", "LOAN-CEILING", "MAX-PRINCIPAL"], ["ML-AMT", "WS-MXLN", "M-PR"], "MAXIMUM LOAN AMOUNT", init=0),
    "mi_required": FieldDef("X", ["MI-REQUIRED", "MORT-INS-IND"], ["MI-IND", "WS-MIR", "M-II"], "MORTGAGE INSURANCE REQUIRED", {"Y": "REQUIRED"}, "N"),
    "rate_adjustment": FieldDef("9V9999", ["RATE-ADJ", "RATE-ADJUSTMENT", "RATE-LOAD"], ["RA-RT", "WS-RADJ", "R-LD"], "RATE ADJUSTMENT", init=0),
    "eligible_flag": FieldDef("X", ["ELIGIBLE-FLAG", "ELIG-IND", "LOAN-ELIGIBLE"], ["EL-IND", "WS-ELG", "L-EL"], "LOAN ELIGIBILITY Y/N", {"Y": "ELIGIBLE"}, "N"),
    "max_term": FieldDef("9(2)", ["MAX-TERM-YRS", "TERM-CAP", "MAX-TERM"], ["MT-YY", "WS-MTRM", "T-CP"], "MAXIMUM TERM IN YEARS", init=0),
    "penalty_amt": FieldDef("9(5)V99", ["PENALTY-AMT", "LATE-FEE", "LATE-CHARGE"], ["PA-AMT", "WS-PEN", "L-CG"], "LATE PAYMENT PENALTY", init=0),
    "account_status": FieldDef("X", ["ACCT-STATUS", "ACCOUNT-STAT", "ACCT-STAT-CD"], ["AS-CD", "WS-AST", "A-SC"], "A=ACTIVE S=SUSPENDED C=COLLECTIONS", {"S": "SUSPENDED", "C": "COLLECTIONS"}, "A"),
    "penalty_waived": FieldDef("X", ["PENALTY-WAIVED", "WAIVER-IND", "PEN-WAIVE-FLG"], ["PW-IND", "WS-PWV", "P-WF"], "PENALTY WAIVER FLAG", {"Y": "WAIVED"}, "N"),
}


# ------------------------------------------------------------- builders ---

def L(field_: str, op: str, value: Any) -> dict[str, Any]:
    return {"field": field_, "op": op, "value": value}


def ALL(*items: dict[str, Any]) -> dict[str, Any]:
    return {"all": list(items)}


def ANY(*items: dict[str, Any]) -> dict[str, Any]:
    return {"any": list(items)}


def SET(target: str, value: Any = None, *, from_: Optional[str] = None) -> dict[str, Any]:
    return {"set": target, "from": from_} if from_ else {"set": target, "value": value}


def COMPUTE(target: str, expr: str) -> dict[str, Any]:
    return {"compute": target, "expr": expr}


def ADD(target: str, value: Any) -> dict[str, Any]:
    return {"add": target, "value": value}


def CALL(program: str) -> dict[str, Any]:
    return {"call": program}


def rule(title: str, intent: str, concepts: list[str], when: Optional[dict], then: list[dict],
         else_: Optional[list[dict]] = None) -> dict[str, Any]:
    out = {"title": title, "intent": intent, "concepts": concepts, "when": when, "then": then}
    if else_:
        out["else"] = else_
    return out


def block(structure: str, names: list[str], rules: list[dict], renders: Optional[list[str]] = None,
          outer: Optional[dict] = None, outer_else: Optional[dict] = None) -> dict[str, Any]:
    out = {"structure": structure, "paragraphs": names, "rules": rules}
    if renders:
        out["renders"] = renders
    if outer is not None:
        out["outer"] = outer
    if outer_else is not None:
        out["outer_else"] = outer_else
    return out


def money(v: float) -> str:
    return f"{v:,.0f}" if float(v).is_integer() else f"{v:,.2f}"


def pct(v: float) -> str:
    return f"{v * 100:.2f}".rstrip("0").rstrip(".") + "%"


def rate(v: float) -> float:
    return round(v, 4)


CHAIN = ["else_if", "evaluate_true"]
CHAIN_SUBJECT = ["evaluate_subject", "evaluate_true", "else_if"]


@dataclass
class Template:
    id: str
    domain: str
    inputs: list[str]
    outputs: list[str]
    build: Callable[[random.Random], list[dict[str, Any]]]


# ----------------------------------------------------------- fraud_scoring ---

def fraud_minor_high_value(r: random.Random) -> list[dict]:
    age = r.choice([16, 18, 18, 18, 21])
    amt = r.choice(range(1000, 10001, 500))
    blocks = [block("if", ["CHECK-TXN", "2000-CHECK-MINOR", "MINOR-TXN-CHECK"], [rule(
        "High-value transaction by minor",
        f"Flag transactions over {money(amt)} made by customers younger than {age} for manual review.",
        ["age", "amount", "minor", "review"],
        ALL(L("customer_age", "<", age), L("transaction_amount", ">", amt)),
        [SET("review_flag", "Y")])])]
    if r.random() < 0.6:
        big = r.choice(range(20000, 90001, 5000))
        codes = sorted(r.sample(["XA", "XB", "XC", "XD", "XE"], k=r.choice([1, 2])))
        acts = [SET("review_flag", "Y")]
        if r.random() < 0.4:
            acts.append(CALL("FRDALERT"))
        geo = L("country_code", "in", codes) if len(codes) > 1 else L("country_code", "==", codes[0])
        blocks.append(block("if", ["CHECK-REGION", "2100-REGION-CHECK", "HIGH-RISK-REGION"], [rule(
            "Large transaction from high-risk region",
            f"Flag transactions over {money(big)} that originate from high-risk country codes {', '.join(codes)}"
            + (" and raise a fraud alert." if len(acts) > 1 else " for review."),
            ["amount", "geography", "country", "review"] + (["alert"] if len(acts) > 1 else []),
            ALL(L("transaction_amount", ">", big), geo), acts)]))
    return blocks


def fraud_velocity(r: random.Random) -> list[dict]:
    c1 = r.choice([15, 20, 25, 30])
    m = r.choice(range(2000, 8001, 1000))
    c2 = r.choice([6, 8, 10, 12])
    d = r.choice([30, 60, 90])
    return [block("chain", ["SCORE-VELOCITY", "3000-VELOCITY-SCORE", "RATE-ACTIVITY"], [
        rule("Very high transaction velocity",
             f"Rate activity as high risk when more than {c1} transactions in 24 hours include one above {money(m)}.",
             ["velocity", "transaction count", "amount", "risk"],
             ALL(L("txn_count_24h", ">", c1), L("transaction_amount", ">", m)),
             [SET("risk_level", "H"), SET("risk_score", 90)]),
        rule("Elevated transaction velocity",
             f"Rate activity as medium risk when the customer made more than {c2} transactions in the last 24 hours.",
             ["velocity", "transaction count", "risk"],
             L("txn_count_24h", ">", c2), [SET("risk_level", "M"), SET("risk_score", 60)]),
        rule("Activity on new account",
             f"Rate activity on accounts opened less than {d} days ago as medium risk.",
             ["account age", "new account", "risk"],
             L("account_age_days", "<", d), [SET("risk_level", "M"), SET("risk_score", 40)]),
        rule("Normal activity", "Rate all other activity as low risk.", ["risk", "default"],
             None, [SET("risk_level", "L"), SET("risk_score", 10)]),
    ], renders=CHAIN)]


def fraud_channel(r: random.Random) -> list[dict]:
    m = r.choice(range(500, 3001, 250))
    m2 = r.choice(range(5000, 20001, 2500))
    outer_else = None
    if r.random() < 0.5:
        outer_else = rule("Non-online transaction", "Never hold transactions made outside the online channel.",
                          ["channel", "hold"], None, [SET("hold_flag", "N")])
    return [block("nested", ["CHECK-CHANNEL", "4000-CHANNEL-RULES", "ONLINE-SCREEN"], [
        rule("Card-not-present online purchase",
             f"Hold online card-not-present transactions above {money(m)} and send them for review.",
             ["channel", "online", "card not present", "amount", "hold"],
             ALL(L("card_present", "==", "N"), L("transaction_amount", ">", m)),
             [SET("hold_flag", "Y"), SET("review_flag", "Y")]),
        rule("Large online transaction",
             f"Send online transactions above {money(m2)} for review.",
             ["channel", "online", "amount", "review"],
             L("transaction_amount", ">", m2), [SET("review_flag", "Y")]),
    ], renders=CHAIN, outer=L("channel_code", "==", "O"), outer_else=outer_else)]


# ---------------------------------------------------------- interest_tiers ---

def interest_balance_tiers(r: random.Random) -> list[dict]:
    b1 = r.choice([50000, 75000, 100000])
    b2 = r.choice([10000, 20000, 25000])
    r1 = r.choice([0.0450, 0.0425, 0.0475])
    r2, r3, rd = rate(r1 - 0.01), rate(r1 - 0.0175), r.choice([0.0050, 0.0100])
    return [block("chain", ["SET-SAVINGS-RATE", "2000-RATE-TIER", "APPLY-INT-TIER"], [
        rule("Top savings tier", f"Pay {pct(r1)} interest on savings accounts with a balance of at least {money(b1)}.",
             ["interest", "savings", "balance", "tier"],
             ALL(L("account_type", "==", "S"), L("balance", ">=", b1)), [SET("interest_rate", r1)]),
        rule("Middle savings tier", f"Pay {pct(r2)} interest on savings accounts with a balance of at least {money(b2)}.",
             ["interest", "savings", "balance", "tier"],
             ALL(L("account_type", "==", "S"), L("balance", ">=", b2)), [SET("interest_rate", r2)]),
        rule("Base savings rate", f"Pay {pct(r3)} interest on all other savings accounts.",
             ["interest", "savings", "account type"], L("account_type", "==", "S"), [SET("interest_rate", r3)]),
        rule("Non-savings rate", f"Pay {pct(rd)} interest on every other account type.",
             ["interest", "account type", "default"], None, [SET("interest_rate", rd)]),
    ], renders=CHAIN)]


def interest_senior_bonus(r: random.Random) -> list[dict]:
    age = r.choice([55, 60, 62, 65])
    bal = r.choice([5000, 10000, 25000])
    bonus = r.choice([0.0025, 0.0050])
    blocks = [block("if", ["SENIOR-BONUS", "2500-SENIOR-RATE", "APPLY-BONUS"], [rule(
        "Senior saver bonus",
        f"Add {pct(bonus)} to the base rate for customers aged {age} or over with at least {money(bal)} on deposit; "
        "everyone else gets the base rate.",
        ["interest", "age", "senior", "balance", "bonus"],
        ALL(L("customer_age", ">=", age), L("balance", ">=", bal)),
        [COMPUTE("bonus_rate", f"base_rate + {bonus:.4f}")], [SET("bonus_rate", from_="base_rate")])])]
    if r.random() < 0.5:
        up = r.choice([0.0010, 0.0015])
        blocks.append(block("if", ["FIXED-UPLIFT", "2600-FIXED-DEP", "FIXED-TERM-BONUS"], [rule(
            "Fixed deposit uplift", f"Add a further {pct(up)} to the rate for fixed deposit accounts.",
            ["interest", "fixed deposit", "account type", "bonus"],
            L("account_type", "==", "F"), [COMPUTE("bonus_rate", f"bonus_rate + {up:.4f}")])]))
    return blocks


def interest_term(r: random.Random) -> list[dict]:
    a = r.choice([3, 6])
    b = r.choice([12, 18])
    c = r.choice([36, 60])
    ra = r.choice([0.0275, 0.0300])
    rb, rc, rd = rate(ra + 0.0075), rate(ra + 0.0150), r.choice([0.0150, 0.0200])
    return [block("chain", ["SET-TERM-RATE", "3000-TERM-RATE", "TERM-DEPOSIT-RATE"], [
        rule(f"Short term deposit rate", f"Pay {pct(ra)} on deposits with a term of 1 to {a} months.",
             ["interest", "term", "deposit"], L("term_months", "between", [1, a]), [SET("interest_rate", ra)]),
        rule(f"Medium term deposit rate", f"Pay {pct(rb)} on deposits with a term of {a + 1} to {b} months.",
             ["interest", "term", "deposit"], L("term_months", "between", [a + 1, b]), [SET("interest_rate", rb)]),
        rule(f"Long term deposit rate", f"Pay {pct(rc)} on deposits with a term of {b + 1} to {c} months.",
             ["interest", "term", "deposit"], L("term_months", "between", [b + 1, c]), [SET("interest_rate", rc)]),
        rule("Other deposit terms", f"Pay {pct(rd)} on deposits with any other term.",
             ["interest", "term", "default"], None, [SET("interest_rate", rd)]),
    ], renders=CHAIN_SUBJECT)]


# ---------------------------------------------------------- fee_exemptions ---

def fee_waiver_balance(r: random.Random) -> list[dict]:
    bal = r.choice([1500, 2500, 5000])
    fee = r.choice([5, 7.5, 10, 12])
    return [block("if", ["WAIVE-FEE", "2000-FEE-WAIVER", "MONTHLY-FEE-CHECK"], [rule(
        "Monthly fee waiver",
        f"Waive the monthly fee for premium or gold accounts and for accounts averaging at least {money(bal)}; "
        f"charge {money(fee)} otherwise.",
        ["fee", "waiver", "account type", "balance"],
        ANY(L("account_type", "in", ["G", "P"]), L("avg_balance", ">=", bal)),
        [SET("fee_waived", "Y"), SET("monthly_fee", 0)], [SET("monthly_fee", fee)])])]


def fee_transaction_count(r: random.Random) -> list[dict]:
    n = r.choice([10, 15, 20, 25])
    per = r.choice([0.25, 0.50, 0.75])
    n2 = n + r.choice([20, 30])
    flat = r.choice([10, 15, 25])
    return [
        block("if", ["EXCESS-TXN-FEE", "3000-COUNT-FEE", "CHARGE-EXCESS"], [rule(
            "Excess transaction fee", f"Charge {money(per)} for each transaction above {n} in the month.",
            ["fee", "transaction count", "excess"],
            L("txn_count_month", ">", n), [COMPUTE("excess_fee", f"(txn_count_month - {n}) * {per:.2f}")])]),
        block("if", ["BUSINESS-SURCHARGE", "3100-BUS-SURCHG", "HIGH-VOLUME-FEE"], [rule(
            "Business high-volume surcharge",
            f"Add a {money(flat)} surcharge for business accounts with more than {n2} transactions in the month.",
            ["fee", "business", "account type", "transaction count"],
            ALL(L("account_type", "==", "B"), L("txn_count_month", ">", n2)), [ADD("excess_fee", flat)])]),
    ]


def fee_student_senior(r: random.Random) -> list[dict]:
    sage = r.choice([24, 25, 26])
    senior = r.choice([60, 62, 65])
    fee = r.choice([4, 6, 9])
    return [block("chain", ["FEE-CONCESSION", "2200-CONCESSIONS", "AGE-FEE-RULES"], [
        rule("Student fee waiver", f"Waive the monthly fee for students younger than {sage}.",
             ["fee", "waiver", "student", "age"],
             ALL(L("student_flag", "==", "Y"), L("customer_age", "<", sage)),
             [SET("fee_waived", "Y"), SET("monthly_fee", 0)]),
        rule("Senior fee waiver", f"Waive the monthly fee for customers aged {senior} or over.",
             ["fee", "waiver", "senior", "age"], L("customer_age", ">=", senior),
             [SET("fee_waived", "Y"), SET("monthly_fee", 0)]),
        rule("Standard monthly fee", f"Charge everyone else the standard monthly fee of {money(fee)}.",
             ["fee", "default"], None, [SET("monthly_fee", fee)]),
    ], renders=CHAIN)]


# ---------------------------------------------------------- credit_approval ---

def credit_score_cutoff(r: random.Random) -> list[dict]:
    s1 = r.choice([700, 720, 740, 760])
    dti = r.choice([0.35, 0.40, 0.43])
    s2 = r.choice([620, 640, 660, 680])
    return [block("chain", ["DECIDE-APPLICATION", "2000-SCORE-DECISION", "CREDIT-CUTOFF"], [
        rule("Automatic approval",
             f"Approve applicants with a credit score of {s1} or more and a debt-to-income ratio below {dti:.2f}.",
             ["credit score", "debt ratio", "approval"],
             ALL(L("credit_score", ">=", s1), L("debt_ratio", "<", dti)), [SET("decision", "A")]),
        rule("Refer to underwriter", f"Refer applicants scoring {s2} or more to an underwriter.",
             ["credit score", "referral", "underwriting"], L("credit_score", ">=", s2), [SET("decision", "R")]),
        rule("Decline application", "Decline all other applicants.", ["decline", "default"],
             None, [SET("decision", "D")]),
    ], renders=CHAIN)]


def credit_bankruptcy(r: random.Random) -> list[dict]:
    yrs = r.choice([5, 7, 10])
    outer_else = None
    if r.random() < 0.5:
        outer_else = rule("No bankruptcy on file", "Record reason code NA when the applicant has no prior bankruptcy.",
                          ["bankruptcy", "reason code"], None, [SET("reason_code", "NA")])
    return [block("nested", ["BANKRUPTCY-CHECK", "2500-BK-RULES", "PRIOR-BK-SCREEN"], [
        rule("Recent bankruptcy decline", f"Decline applicants with a bankruptcy in the last {yrs} years.",
             ["bankruptcy", "decline", "credit history"], L("years_since_bk", "<", yrs),
             [SET("decision", "D"), SET("reason_code", "BK1")]),
        rule("Older bankruptcy referral", f"Refer applicants whose bankruptcy is {yrs} or more years old.",
             ["bankruptcy", "referral", "credit history"], None,
             [SET("decision", "R"), SET("reason_code", "BK2")]),
    ], renders=CHAIN, outer=L("bankruptcy_flag", "==", "Y"), outer_else=outer_else)]


def credit_limit_assign(r: random.Random) -> list[dict]:
    s1 = r.choice([740, 750, 760])
    s2 = r.choice([640, 660, 680])
    p1 = r.choice([0.25, 0.30])
    p2 = r.choice([0.15, 0.20])
    floor = r.choice([500, 1000])
    return [block("chain", ["ASSIGN-LIMIT", "3000-CREDIT-LIMIT", "SET-CARD-LIMIT"], [
        rule("Prime credit limit", f"Set the credit limit to {int(p1 * 100)}% of annual income for scores of {s1} or more.",
             ["credit limit", "credit score", "income"], L("credit_score", ">=", s1),
             [COMPUTE("credit_limit", f"annual_income * {p1:.2f}")]),
        rule("Standard credit limit", f"Set the credit limit to {int(p2 * 100)}% of annual income for scores of {s2} or more.",
             ["credit limit", "credit score", "income"], L("credit_score", ">=", s2),
             [COMPUTE("credit_limit", f"annual_income * {p2:.2f}")]),
        rule("Minimum credit limit", f"Give every other approved applicant the minimum limit of {money(floor)}.",
             ["credit limit", "default"], None, [SET("credit_limit", floor)]),
    ], renders=CHAIN)]


# ------------------------------------------------------------- loan_limits ---

def loan_income_multiple(r: random.Random) -> list[dict]:
    y1 = r.choice([5, 7])
    y2 = r.choice([2, 3])
    m1 = r.choice([4, 5])
    m3 = r.choice(["1.5", "2"])
    return [block("chain", ["MAX-LOAN-CALC", "2000-LOAN-MULTIPLE", "INCOME-MULTIPLE"], [
        rule("Established employment multiple", f"Lend up to {m1} times annual income to applicants employed {y1} years or more.",
             ["loan limit", "income", "employment"], L("employment_years", ">=", y1),
             [COMPUTE("max_loan", f"annual_income * {m1}")]),
        rule("Standard employment multiple", f"Lend up to 3 times annual income to applicants employed {y2} years or more.",
             ["loan limit", "income", "employment"], L("employment_years", ">=", y2),
             [COMPUTE("max_loan", "annual_income * 3")]),
        rule("New employee multiple", f"Lend up to {m3} times annual income to everyone else.",
             ["loan limit", "income", "default"], None, [COMPUTE("max_loan", f"annual_income * {m3}")]),
    ], renders=CHAIN)]


def loan_ltv(r: random.Random) -> list[dict]:
    lim = r.choice([80, 85, 90])
    adj = r.choice([0.0025, 0.0050])
    return [block("if", ["CHECK-LTV", "3000-LTV-RULES", "MORTGAGE-INS-CHECK"], [rule(
        "Mortgage insurance requirement",
        f"Require mortgage insurance and add {pct(adj)} to the rate when the loan-to-value ratio exceeds {lim}%.",
        ["loan to value", "mortgage insurance", "rate"],
        L("ltv_pct", ">", lim), [SET("mi_required", "Y"), SET("rate_adjustment", adj)], [SET("mi_required", "N")])])]


def loan_age_term(r: random.Random) -> list[dict]:
    a1 = r.choice([60, 65])
    a2 = r.choice([50, 55])
    return [block("nested", ["TERM-BY-AGE", "2500-AGE-TERM", "LOAN-TERM-RULES"], [
        rule("Short term for older borrowers", f"Cap the loan term at 10 years for borrowers older than {a1}.",
             ["loan term", "age", "senior"], L("customer_age", ">", a1),
             [SET("max_term", 10), SET("eligible_flag", "Y")]),
        rule("Medium term cap", f"Cap the loan term at 20 years for borrowers older than {a2}.",
             ["loan term", "age"], L("customer_age", ">", a2),
             [SET("max_term", 20), SET("eligible_flag", "Y")]),
        rule("Full term", "Allow the full 30-year term for all other adult borrowers.",
             ["loan term", "default"], None, [SET("max_term", 30), SET("eligible_flag", "Y")]),
    ], renders=CHAIN, outer=L("customer_age", ">=", 18),
        outer_else=rule("Underage applicant", "Mark applicants younger than 18 as ineligible.",
                        ["eligibility", "age", "minor"], None, [SET("eligible_flag", "N")]))]


# ------------------------------------------------------ penalty_assessment ---

def penalty_days_late(r: random.Random) -> list[dict]:
    a = r.choice([10, 15])
    b = r.choice([30, 45])
    f1 = r.choice([15, 25])
    f2 = r.choice([35, 50])
    pctl = r.choice([0.05, 0.08])
    return [block("chain", ["ASSESS-PENALTY", "2000-LATE-PENALTY", "CALC-LATE-FEE"], [
        rule("Early late payment fee", f"Charge {money(f1)} when a payment is 1 to {a} days late.",
             ["penalty", "days late", "late fee"], L("days_late", "between", [1, a]), [SET("penalty_amt", f1)]),
        rule("Late payment fee", f"Charge {money(f2)} when a payment is {a + 1} to {b} days late.",
             ["penalty", "days late", "late fee"], L("days_late", "between", [a + 1, b]), [SET("penalty_amt", f2)]),
        rule("Seriously late payment penalty",
             f"Charge {int(pctl * 100)}% of the amount due when a payment is more than {b} days late.",
             ["penalty", "days late", "amount due"], L("days_late", "between", [b + 1, 999]),
             [COMPUTE("penalty_amt", f"balance_due * {pctl:.2f}")]),
        rule("No penalty", "Charge no penalty when the payment is not late.", ["penalty", "default"],
             None, [SET("penalty_amt", 0)]),
    ], renders=CHAIN_SUBJECT)]


def penalty_waiver(r: random.Random) -> list[dict]:
    g = r.choice([3, 5, 7])
    return [block("nested", ["FIRST-OFFENSE-WAIVER", "3000-WAIVER-RULES", "WAIVE-PENALTY"], [
        rule("Premium first-offence waiver", "Waive the penalty on a first late payment for gold and platinum customers.",
             ["penalty", "waiver", "first offence", "customer tier"],
             L("customer_tier", "in", ["G", "P"]), [SET("penalty_waived", "Y"), SET("penalty_amt", 0)]),
        rule("Grace period waiver", f"Waive the penalty on a first late payment made within {g} days.",
             ["penalty", "waiver", "first offence", "grace period"],
             L("days_late", "<=", g), [SET("penalty_waived", "Y"), SET("penalty_amt", 0)]),
    ], renders=CHAIN, outer=L("first_offense", "==", "Y"))]


def penalty_repeat(r: random.Random) -> list[dict]:
    k = r.choice([3, 4, 5])
    d = r.choice([30, 45])
    d2 = r.choice([60, 90])
    add = r.choice([50, 100])
    return [
        block("if", ["REPEAT-OFFENDER", "4000-REPEAT-LATE", "SUSPEND-CHECK"], [rule(
            "Repeat late payer suspension",
            f"Suspend the account and add a {money(add)} penalty when a customer with {k} or more late payments "
            f"in 12 months is over {d} days late.",
            ["penalty", "repeat", "suspension", "days late"],
            ALL(L("late_count_12m", ">=", k), L("days_late", ">", d)),
            [SET("account_status", "S"), ADD("penalty_amt", add)])]),
        block("if", ["COLLECTIONS-CHECK", "4100-TO-COLLECTIONS", "REFER-COLLECTIONS"], [rule(
            "Collections referral", f"Move accounts more than {d2} days late to collections.",
            ["collections", "days late", "account status"],
            L("days_late", ">", d2), [SET("account_status", "C")])]),
    ]


# -------------------------------------------------------------- registry ---

TEMPLATES: list[Template] = [
    # fraud_scoring
    Template("fraud_minor_high_value", "fraud_scoring", ["customer_age", "transaction_amount", "country_code"], ["review_flag"], fraud_minor_high_value),
    Template("fraud_velocity", "fraud_scoring", ["txn_count_24h", "transaction_amount", "account_age_days"], ["risk_level", "risk_score"], fraud_velocity),
    Template("fraud_channel", "fraud_scoring", ["channel_code", "card_present", "transaction_amount"], ["hold_flag", "review_flag"], fraud_channel),
    # interest_tiers
    Template("interest_balance_tiers", "interest_tiers", ["account_type", "balance"], ["interest_rate"], interest_balance_tiers),
    Template("interest_senior_bonus", "interest_tiers", ["customer_age", "balance", "account_type", "base_rate"], ["bonus_rate"], interest_senior_bonus),
    Template("interest_term", "interest_tiers", ["term_months"], ["interest_rate"], interest_term),
    # fee_exemptions
    Template("fee_waiver_balance", "fee_exemptions", ["account_type", "avg_balance"], ["fee_waived", "monthly_fee"], fee_waiver_balance),
    Template("fee_transaction_count", "fee_exemptions", ["account_type", "txn_count_month"], ["excess_fee"], fee_transaction_count),
    Template("fee_student_senior", "fee_exemptions", ["student_flag", "customer_age"], ["fee_waived", "monthly_fee"], fee_student_senior),
    # credit_approval
    Template("credit_score_cutoff", "credit_approval", ["credit_score", "debt_ratio"], ["decision"], credit_score_cutoff),
    Template("credit_bankruptcy", "credit_approval", ["bankruptcy_flag", "years_since_bk", "credit_score"], ["decision", "reason_code"], credit_bankruptcy),
    Template("credit_limit_assign", "credit_approval", ["credit_score", "annual_income"], ["credit_limit"], credit_limit_assign),
    # loan_limits
    Template("loan_income_multiple", "loan_limits", ["employment_years", "annual_income"], ["max_loan"], loan_income_multiple),
    Template("loan_age_term", "loan_limits", ["customer_age"], ["max_term", "eligible_flag"], loan_age_term),
    Template("loan_ltv", "loan_limits", ["ltv_pct"], ["mi_required", "rate_adjustment"], loan_ltv),
    # penalty_assessment
    Template("penalty_days_late", "penalty_assessment", ["days_late", "balance_due"], ["penalty_amt"], penalty_days_late),
    Template("penalty_waiver", "penalty_assessment", ["first_offense", "customer_tier", "days_late"], ["penalty_waived", "penalty_amt"], penalty_waiver),
    Template("penalty_repeat", "penalty_assessment", ["late_count_12m", "days_late"], ["account_status", "penalty_amt"], penalty_repeat),
]

DOMAIN_PREFIX = {
    "fraud_scoring": "FRD", "interest_tiers": "INT", "fee_exemptions": "FEE",
    "credit_approval": "CRD", "loan_limits": "LON", "penalty_assessment": "PEN",
}

# Split by template so near-duplicates never cross splits (implementation.md 3.4):
# per domain, the first two templates train; the third alternates validation / test.
SPLIT_OF_TEMPLATE: dict[str, str] = {}
for _i, _domain in enumerate(DOMAIN_PREFIX):
    _ts = [t.id for t in TEMPLATES if t.domain == _domain]
    SPLIT_OF_TEMPLATE.update({_ts[0]: "train", _ts[1]: "train", _ts[2]: "val" if _i % 2 == 0 else "test"})
