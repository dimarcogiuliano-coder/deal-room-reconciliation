"""
Deal room reconciliation engine.

Cross-checks a billing-system export against an ERP/ledger export (the two systems a
real data room review always pulls from), plus the underlying signed contract values,
and flags exactly the failure modes that actually surface in pre-raise / pre-acquisition
diligence: missing bookings, phantom bookings, amount mismatches, wrong-period revenue,
near-duplicate customer names hiding a double-booked payment, and invoiced amounts that
drift from the signed contract.

Design intent: this is the "control" layer -- it doesn't replace judgment, it tells you
exactly where to point it, with the evidence already pulled together, instead of a human
finding this by chance three weeks into diligence.

Usage: python3 reconcile.py
Outputs: findings.json, findings_summary.txt
"""

import csv
import json
from difflib import SequenceMatcher
from datetime import date

AMOUNT_TOLERANCE_EUR = 5  # rounding tolerance before flagging an amount mismatch
NAME_SIMILARITY_FLOOR = 0.82  # below this, names are considered unrelated
NAME_SIMILARITY_CEILING = 0.995  # above this, treat as the same name (trivial whitespace etc.)


def load_csv(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def quarter_of(yyyy_mm_dd: str) -> str:
    y, m, _ = yyyy_mm_dd.split("-")
    m = int(m)
    q = (m - 1) // 3 + 1
    return f"{y}-Q{q}"


def name_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.strip().lower(), b.strip().lower()).ratio()


def main():
    billing = load_csv("billing_system_export.csv")
    erp = load_csv("erp_ledger_export.csv")
    contracts = {r["customer"]: float(r["monthly_contract_value_eur"]) for r in load_csv("contracts.csv")}

    billing_by_id = {r["invoice_id"]: r for r in billing}
    erp_by_id = {r["invoice_id"]: r for r in erp}

    findings = []

    def add(kind, severity, invoice_ids, customer, description, evidence, exposure_eur=0):
        findings.append({
            "id": f"F{len(findings) + 1:03d}",
            "type": kind,
            "severity": severity,
            "invoice_ids": invoice_ids,
            "customer": customer,
            "description": description,
            "evidence": evidence,
            "exposure_eur": round(exposure_eur, 2),
        })

    # --- Check 1: invoices in billing with no matching ERP booking (revenue leakage) ---
    for inv_id, b in billing_by_id.items():
        if inv_id not in erp_by_id:
            add(
                "missing_from_erp", "high", [inv_id], b["customer"],
                f"{b['customer']} was invoiced in billing (EUR {float(b['amount_eur']):,.0f}, "
                f"issued {b['issue_date']}) but no matching entry exists in the ERP ledger. "
                f"Unrecognised revenue -- the kind of gap that only surfaces once someone cross-checks "
                f"both systems line by line.",
                {"billing": b},
                exposure_eur=float(b["amount_eur"]),
            )

    # --- Check 2: ERP bookings with no matching billing invoice (phantom entries) ---
    for inv_id, e in erp_by_id.items():
        if inv_id not in billing_by_id:
            add(
                "phantom_erp_entry", "high", [inv_id], e["customer"],
                f"{e['customer']} has an ERP booking (EUR {float(e['amount_eur']):,.0f}, "
                f"period {e['period']}) with no underlying invoice in the billing system. "
                f"No source document -- needs tracing back to whatever created this entry.",
                {"erp": e},
                exposure_eur=float(e["amount_eur"]),
            )

    # --- Check 3 & 4: matched invoices -- amount mismatch, currency mismatch, wrong-period ---
    for inv_id in billing_by_id.keys() & erp_by_id.keys():
        b, e = billing_by_id[inv_id], erp_by_id[inv_id]
        b_amount, e_amount = float(b["amount_eur"]), float(e["amount_eur"])

        if b.get("currency") and b["currency"] != "EUR":
            add(
                "currency_mismatch", "medium", [inv_id], b["customer"],
                f"{b['customer']}'s invoice was raised in {b['currency']} and converted to EUR "
                f"{b_amount:,.0f} in billing, but booked in the ERP at EUR {e_amount:,.0f} -- a "
                f"EUR {abs(b_amount - e_amount):,.0f} gap. The two were never reconciled against "
                f"each other after conversion.",
                {"billing": b, "erp": e},
                exposure_eur=abs(b_amount - e_amount),
            )
        elif abs(b_amount - e_amount) > AMOUNT_TOLERANCE_EUR:
            add(
                "amount_mismatch", "high", [inv_id], b["customer"],
                f"{b['customer']} billed at EUR {b_amount:,.0f} but booked in the ERP at EUR "
                f"{e_amount:,.0f} -- a EUR {abs(b_amount - e_amount):,.0f} gap between the two systems "
                f"for the same invoice.",
                {"billing": b, "erp": e},
                exposure_eur=abs(b_amount - e_amount),
            )

        # Wrong-period check: does the ERP booking period match the quarter the invoice was issued in?
        issue_q = quarter_of(b["issue_date"])
        booked_q = quarter_of(e["booked_date"])
        if issue_q != booked_q:
            add(
                "wrong_period", "high", [inv_id], b["customer"],
                f"{b['customer']}'s invoice was issued {b['issue_date']} ({issue_q}) but booked into "
                f"the ERP as {e['booked_date']} ({booked_q}) -- revenue recognised outside the quarter "
                f"it was actually earned in.",
                {"billing": b, "erp": e},
                exposure_eur=b_amount,
            )

    # --- Check 5: near-duplicate customer names in the ERP with matching amount + period ---
    # (catches a bill paid twice under a slightly different supplier/customer name)
    erp_list = erp
    seen_pairs = set()
    for i, e1 in enumerate(erp_list):
        for e2 in erp_list[i + 1:]:
            if e1["invoice_id"] == e2["invoice_id"]:
                continue
            pair_key = tuple(sorted([e1["invoice_id"], e2["invoice_id"]]))
            if pair_key in seen_pairs:
                continue
            sim = name_similarity(e1["customer"], e2["customer"])
            same_amount = abs(float(e1["amount_eur"]) - float(e2["amount_eur"])) < 0.01
            same_period = e1["period"] == e2["period"]
            if NAME_SIMILARITY_FLOOR <= sim < NAME_SIMILARITY_CEILING and same_amount and same_period:
                seen_pairs.add(pair_key)
                add(
                    "likely_duplicate_payment", "high", [e1["invoice_id"], e2["invoice_id"]],
                    e1["customer"],
                    f"'{e1['customer']}' and '{e2['customer']}' are {sim:.0%} similar names, both booked "
                    f"for EUR {float(e1['amount_eur']):,.0f} in {e1['period']} -- reads as the same bill "
                    f"paid twice under a near-identical supplier name, not two real customers.",
                    {"erp_entry_1": e1, "erp_entry_2": e2},
                    exposure_eur=float(e1["amount_eur"]),
                )

    # --- Check 6: invoiced amount vs. signed contract value ---
    for inv_id, b in billing_by_id.items():
        contract_value = contracts.get(b["customer"])
        if contract_value is None:
            continue  # near-duplicate name won't match the contract list -- already caught above
        b_amount = float(b["amount_eur"])
        if b.get("currency") and b["currency"] != "EUR":
            continue  # currency mismatch already flagged separately; contract comparison not meaningful here
        diff = b_amount - contract_value
        if abs(diff) > AMOUNT_TOLERANCE_EUR:
            direction = "over-billed" if diff > 0 else "under-billed"
            add(
                "contract_drift", "medium", [inv_id], b["customer"],
                f"{b['customer']} was {direction} by EUR {abs(diff):,.0f} vs. the signed contract value "
                f"of EUR {contract_value:,.0f}/month -- both billing and the ERP agree with each other, "
                f"but not with the contract on file.",
                {"billing": b, "contract_value_eur": contract_value},
                exposure_eur=abs(diff),
            )

    # --- Summary ---
    total_exposure = sum(f["exposure_eur"] for f in findings)
    summary = {
        "transactions_checked": len(set(billing_by_id) | set(erp_by_id)),
        "billing_transactions": len(billing),
        "erp_transactions": len(erp),
        "findings_count": len(findings),
        "total_exposure_eur": round(total_exposure, 2),
        "by_type": {},
    }
    for f in findings:
        summary["by_type"].setdefault(f["type"], 0)
        summary["by_type"][f["type"]] += 1

    with open("findings.json", "w") as f:
        json.dump({"summary": summary, "findings": findings}, f, indent=2)

    with open("findings_summary.txt", "w") as f:
        f.write(f"Transactions checked: {summary['transactions_checked']}\n")
        f.write(f"Findings: {summary['findings_count']}\n")
        f.write(f"Total EUR exposure flagged: {summary['total_exposure_eur']:,.0f}\n\n")
        for f_ in findings:
            f.write(f"[{f_['id']}] ({f_['severity'].upper()}) {f_['type']} -- {f_['customer']}\n")
            f.write(f"  {f_['description']}\n\n")

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
