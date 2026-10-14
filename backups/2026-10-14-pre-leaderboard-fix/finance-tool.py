"""finance - deterministic financial math for the 'Code Challenge' tiles.

Why a tool: the generic calc tool runs whatever Python the model writes, and the
model got a compound-interest question wrong (it printed 412431.15 for a $200,000
/ 3.5% / daily / 20yr future value; the correct value is 402737.03) and lost a
life. This tool removes the model's chance to mis-derive a formula: it passes the
OPERATION and the NUMBERS, and the Lambda computes the exact answer.

Grader wants the bare number: no "$", no thousands separators. That's what the
answer field contains, e.g. "402737.03".
Standard library only.
"""

import json
import math

TOOL_VERSION = "2026-10-13.finance.1"

# how many compounding periods per year each name means
PERIODS = {
    "daily": 365, "day": 365, "365": 365,
    "weekly": 52, "week": 52,
    "monthly": 12, "month": 12, "12": 12,
    "quarterly": 4, "quarter": 4, "4": 4,
    "semiannual": 2, "semiannually": 2, "biannual": 2,
    "annually": 1, "annual": 1, "yearly": 1, "year": 1, "1": 1,
    # "continuous" is handled specially below
}


def _parse_body(event):
    if not isinstance(event, dict):
        return {}
    body = event.get("body", event)
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except json.JSONDecodeError:
            return {}
    if isinstance(body, dict) and "parameters" in body and isinstance(body["parameters"], list):
        return {p.get("name"): p.get("value") for p in body["parameters"]}
    return body if isinstance(body, dict) else {}


def _num(x, default=None):
    if x is None:
        return default
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip().replace("$", "").replace(",", "").replace("%", "")
    try:
        return float(s)
    except ValueError:
        return default


def _rate(r):
    """Accept 3.5 (percent) or 0.035 (fraction); return the fraction."""
    r = _num(r, 0.0)
    return r / 100.0 if abs(r) > 1.0 else r


def _periods(compounding):
    c = str(compounding or "daily").strip().lower()
    if "cont" in c:
        return "continuous"
    return PERIODS.get(c, 365)  # default daily


def future_value(principal, rate, years, compounding="daily"):
    P, r, t = _num(principal, 0.0), _rate(rate), _num(years, 0.0)
    n = _periods(compounding)
    if n == "continuous":
        return P * math.exp(r * t)
    return P * (1.0 + r / n) ** (n * t)


def present_value(amount, rate, years, compounding="annually"):
    A, r, t = _num(amount, 0.0), _rate(rate), _num(years, 0.0)
    n = _periods(compounding)
    if n == "continuous":
        return A * math.exp(-r * t)
    return A / (1.0 + r / n) ** (n * t)


def compound_interest(principal, rate, years, compounding="daily"):
    """Just the interest earned (future value minus principal)."""
    return future_value(principal, rate, years, compounding) - _num(principal, 0.0)


def simple_interest(principal, rate, years):
    return _num(principal, 0.0) * _rate(rate) * _num(years, 0.0)


def loan_payment(principal, rate, years, compounding="monthly"):
    P, r, t = _num(principal, 0.0), _rate(rate), _num(years, 0.0)
    n = _periods(compounding)
    if n == "continuous":
        n = 12
    i = r / n
    k = n * t
    if i == 0:
        return P / k
    return P * i / (1.0 - (1.0 + i) ** (-k))


def cagr(start, end, years):
    s, e, t = _num(start, 0.0), _num(end, 0.0), _num(years, 1.0)
    if s <= 0 or t == 0:
        return 0.0
    return (e / s) ** (1.0 / t) - 1.0


OPS = {
    "future_value": future_value, "fv": future_value,
    "present_value": present_value, "pv": present_value,
    "compound_interest": compound_interest,
    "simple_interest": simple_interest,
    "loan_payment": loan_payment, "payment": loan_payment, "pmt": loan_payment,
    "cagr": cagr,
}


def solve(body):
    op = str(body.get("operation") or "future_value").strip().lower()
    fn = OPS.get(op)
    if fn is None:
        raise ValueError(f"unknown operation {op!r}; use one of {sorted(set(OPS))}")

    comp = body.get("compounding") or body.get("compound") or "daily"
    if op in ("future_value", "fv", "compound_interest"):
        val = fn(body.get("principal") or body.get("amount"),
                 body.get("rate"), body.get("years"), comp)
    elif op in ("present_value", "pv"):
        val = fn(body.get("amount") or body.get("principal"),
                 body.get("rate"), body.get("years"), comp)
    elif op in ("loan_payment", "payment", "pmt"):
        val = fn(body.get("principal") or body.get("amount"),
                 body.get("rate"), body.get("years"), comp)
    elif op == "simple_interest":
        val = fn(body.get("principal") or body.get("amount"),
                 body.get("rate"), body.get("years"))
    elif op == "cagr":
        # cagr returns a fraction; report as a percent with 4 decimals
        frac = fn(body.get("start"), body.get("end"), body.get("years"))
        return f"{frac * 100:.4f}"
    else:  # pragma: no cover
        val = fn(body.get("principal"), body.get("rate"), body.get("years"), comp)
    return f"{val:.2f}"  # bare number, 2 decimals, no symbol/commas


def lambda_handler(event, context):
    """Deterministic financial calculator.

    ---
    Tool: finance
    Description: Computes financial answers EXACTLY for money/interest/loan/investment questions - use this instead of writing math code. Emit the answer field verbatim (a bare number, no $ or commas).
    Parameters:
        operation    (required) - one of future_value, present_value, compound_interest, simple_interest, loan_payment, cagr
        principal    - the starting amount (a.k.a. amount)
        rate         - interest rate, percent (3.5) or fraction (0.035)
        years        - number of years
        compounding  - daily, weekly, monthly, quarterly, semiannual, annually, or continuous (default daily)
        start, end   - only for cagr (start value, end value)
    ---
    """
    try:
        body = _parse_body(event)
        answer = solve(body)
        print(f"FINANCE v={TOOL_VERSION} body={body} answer={answer}")
        return {"statusCode": 200, "body": json.dumps({"answer": answer})}
    except Exception as exc:  # noqa: BLE001
        print(f"FINANCE ERROR v={TOOL_VERSION} {exc} event={event}")
        return {"statusCode": 400, "body": json.dumps({"error": str(exc)})}


if __name__ == "__main__":
    # the exact question that lost a life
    q = {"operation": "future_value", "principal": 200000, "rate": 3.5,
         "years": 20, "compounding": "daily"}
    out = json.loads(lambda_handler({"body": q}, None)["body"])["answer"]
    print("FV daily  ->", out)
    assert out == "402737.03", f"expected 402737.03, got {out}"
    # a few more shapes
    print("FV monthly->", json.loads(lambda_handler({"body": {**q, "compounding": "monthly"}}, None)["body"])["answer"])
    print("PMT       ->", json.loads(lambda_handler({"body": {"operation": "loan_payment", "principal": 300000, "rate": 6, "years": 30, "compounding": "monthly"}}, None)["body"])["answer"])
    print("CAGR %    ->", json.loads(lambda_handler({"body": {"operation": "cagr", "start": 100000, "end": 200000, "years": 10}}, None)["body"])["answer"])
    print("self-test OK")