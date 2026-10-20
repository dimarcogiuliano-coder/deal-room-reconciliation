"""
Generates two synthetic exports for a fictional B2B SaaS company ("Nordlys Analytics ApS")
mid-way through a Series A data room: a billing-system export and an ERP/ledger export.

Both describe the same underlying set of ~42 customer invoices for Q2 2026 (Apr-Jun), but
8 specific discrepancies have been deliberately planted -- each one a real, common failure
mode from pre-raise financial diligence:

  1. Duplicate payment booked twice in the ERP under a slightly different supplier/customer name
  2. Revenue recognised in the wrong period (invoice issued before quarter-end, booked after)
  3. Amount mismatch between the billing system and the ERP (manual re-entry error)
  4. Transaction present in billing but missing from the ERP (revenue leakage / unbooked invoice)
  5. Transaction present in the ERP but missing from billing (phantom / erroneously duplicated entry)
  6. Invoice amount doesn't match the signed contract terms (over-billed)
  7. Invoice amount doesn't match the signed contract terms (under-billed)
  8. Currency conversion mismatch beyond reasonable rounding tolerance

This is synthetic data for a portfolio/demo build -- no real company, customer or financial
information is used.
"""

import csv
import random

random.seed(42)

CUSTOMERS = [
    "Fjordmark A/S", "Solheim Retail Group", "Brynja Health Systems", "Kysten Logistics ApS",
    "Nordpunkt Insurance", "Lysglas Manufacturing", "Granitby Consulting", "Havnefront Media",
    "Elvedal Foods A/S", "Strandvik Energy", "Myrland Mobility", "Osterbro Property Group",
    "Kildevang Education", "Ravnholm Capital", "Bjerget Clinics", "Sommerby Hospitality",
    "Vesterled Shipping", "Lindevang Pharma", "Rosendal Retail", "Dalby Construction",
]

CONTRACTS = {
    # customer -> monthly contract value (EUR), per signed terms
    "Fjordmark A/S": 4200, "Solheim Retail Group": 2800, "Brynja Health Systems": 6100,
    "Kysten Logistics ApS": 3300, "Nordpunkt Insurance": 5400, "Lysglas Manufacturing": 2100,
    "Granitby Consulting": 1800, "Havnefront Media": 3900, "Elvedal Foods A/S": 2600,
    "Strandvik Energy": 7200, "Myrland Mobility": 1500, "Osterbro Property Group": 4800,
    "Kildevang Education": 1200, "Ravnholm Capital": 9100, "Bjerget Clinics": 3100,
    "Sommerby Hospitality": 2400, "Vesterled Shipping": 5900, "Lindevang Pharma": 6700,
    "Rosendal Retail": 2000, "Dalby Construction": 3600,
}

MONTHS = [("2026-04", 30), ("2026-05", 31), ("2026-06", 30)]

billing_rows = []
erp_rows = []
inv_counter = 1000
planted = []  # human-readable log of what was planted and why, used to validate the engine later

def new_invoice_id():
    global inv_counter
    inv_counter += 1
    return f"INV-{inv_counter}"

# --- Generate clean baseline: one invoice per customer per month, matching contract value ---
for month, days_in_month in MONTHS:
    for customer in CUSTOMERS:
        amount = CONTRACTS[customer]
        day = random.randint(1, 5)  # invoices typically issued early in the month
        date = f"{month}-{day:02d}"
        inv_id = new_invoice_id()
        billing_rows.append({"invoice_id": inv_id, "customer": customer, "amount_eur": amount,
                              "issue_date": date, "currency": "EUR"})
        erp_rows.append({"invoice_id": inv_id, "customer": customer, "amount_eur": amount,
                          "booked_date": date, "period": month})

# --- Now plant the 8 discrepancies on top of the clean baseline ---

# 1. Duplicate payment booked twice under a slightly different supplier/customer name
dup_source = next(r for r in erp_rows if r["customer"] == "Fjordmark A/S" and r["period"] == "2026-05")
dup_id = new_invoice_id()
erp_rows.append({"invoice_id": dup_id, "customer": "Fjordmark A/S.", "amount_eur": dup_source["amount_eur"],
                  "booked_date": dup_source["booked_date"], "period": dup_source["period"]})
planted.append(f"[1] Duplicate: {dup_source['invoice_id']} (Fjordmark A/S) re-booked as {dup_id} under "
                f"'Fjordmark A/S.' (trailing period) in the ERP, same amount/period -- same bill paid twice "
                f"under a near-identical supplier name.")

# 2. Revenue recognised in the wrong period -- issued late June, booked as July (next quarter)
late_customer = "Ravnholm Capital"
late_row = next(r for r in billing_rows if r["customer"] == late_customer and r["issue_date"].startswith("2026-06"))
late_erp = next(r for r in erp_rows if r["invoice_id"] == late_row["invoice_id"])
late_row["issue_date"] = "2026-06-29"
late_erp["period"] = "2026-07"
late_erp["booked_date"] = "2026-07-02"
planted.append(f"[2] Wrong-period booking: {late_row['invoice_id']} (Ravnholm Capital) issued 2026-06-29 "
                f"(Q2) but booked into the ERP as period 2026-07 (Q3) -- revenue pulled forward out of the "
                f"quarter it was earned in.")

# 3. Amount mismatch -- manual re-entry error
mismatch_customer = "Lindevang Pharma"
mismatch_row = next(r for r in billing_rows if r["customer"] == mismatch_customer and r["issue_date"].startswith("2026-05"))
mismatch_erp = next(r for r in erp_rows if r["invoice_id"] == mismatch_row["invoice_id"])
mismatch_erp["amount_eur"] = 6070  # billing says 6700, ERP says 6070 -- transposed digits
planted.append(f"[3] Amount mismatch: {mismatch_row['invoice_id']} (Lindevang Pharma) billed at EUR "
                f"{mismatch_row['amount_eur']} but booked in the ERP at EUR {mismatch_erp['amount_eur']} -- "
                f"looks like transposed digits on manual entry.")

# 4. Missing from ERP -- invoice exists in billing, never made it into the ledger (revenue leakage)
leak_customer = "Osterbro Property Group"
leak_row = next(r for r in billing_rows if r["customer"] == leak_customer and r["issue_date"].startswith("2026-06"))
erp_rows[:] = [r for r in erp_rows if r["invoice_id"] != leak_row["invoice_id"]]
planted.append(f"[4] Missing from ERP: {leak_row['invoice_id']} (Osterbro Property Group, EUR "
                f"{leak_row['amount_eur']}) was invoiced in billing but never booked in the ERP -- "
                f"unrecognised revenue, the kind that gets found in week 1 of diligence.")

# 5. Missing from billing -- a booked ERP entry with no underlying invoice (phantom / erroneous duplicate)
phantom_id = new_invoice_id()
erp_rows.append({"invoice_id": phantom_id, "customer": "Strandvik Energy", "amount_eur": 7200,
                  "booked_date": "2026-05-18", "period": "2026-05"})
planted.append(f"[5] Phantom ERP entry: {phantom_id} (Strandvik Energy, EUR 7200) booked in the ERP in "
                f"period 2026-05 with no matching invoice in billing -- looks like a duplicate manual "
                f"booking with no source document.")

# 6. Over-billed vs. contract
over_customer = "Kysten Logistics ApS"
over_row = next(r for r in billing_rows if r["customer"] == over_customer and r["issue_date"].startswith("2026-04"))
over_erp = next(r for r in erp_rows if r["invoice_id"] == over_row["invoice_id"])
over_row["amount_eur"] = 3850
over_erp["amount_eur"] = 3850
planted.append(f"[6] Over-billed vs. contract: {over_row['invoice_id']} (Kysten Logistics ApS) invoiced "
                f"at EUR 3850 against a signed contract value of EUR {CONTRACTS[over_customer]} -- EUR "
                f"{3850 - CONTRACTS[over_customer]} over the agreed rate, both systems agree with each "
                f"other but not with the contract.")

# 7. Under-billed vs. contract
under_customer = "Bjerget Clinics"
under_row = next(r for r in billing_rows if r["customer"] == under_customer and r["issue_date"].startswith("2026-06"))
under_erp = next(r for r in erp_rows if r["invoice_id"] == under_row["invoice_id"])
under_row["amount_eur"] = 2600
under_erp["amount_eur"] = 2600
planted.append(f"[7] Under-billed vs. contract: {under_row['invoice_id']} (Bjerget Clinics) invoiced at "
                f"EUR 2600 against a signed contract value of EUR {CONTRACTS[under_customer]} -- EUR "
                f"{CONTRACTS[under_customer] - 2600} short of the agreed rate.")

# 8. Currency conversion mismatch beyond rounding tolerance
fx_customer = "Havnefront Media"
fx_row = next(r for r in billing_rows if r["customer"] == fx_customer and r["issue_date"].startswith("2026-05"))
fx_erp = next(r for r in erp_rows if r["invoice_id"] == fx_row["invoice_id"])
fx_row["currency"] = "DKK"
fx_row["amount_eur"] = 29000  # originally invoiced in DKK, billing system shows the DKK-converted EUR figure
fx_erp["amount_eur"] = 3900   # ERP booked it at the contracted EUR rate directly -- the two were never reconciled
planted.append(f"[8] Currency conversion mismatch: {fx_row['invoice_id']} (Havnefront Media) invoiced in "
                f"DKK and converted to EUR {fx_row['amount_eur']} in billing, but booked in the ERP at EUR "
                f"{fx_erp['amount_eur']} (the contracted EUR rate) -- the DKK invoice and the EUR booking "
                f"were never reconciled against each other.")

# --- Write outputs ---
with open("billing_system_export.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["invoice_id", "customer", "amount_eur", "issue_date", "currency"])
    w.writeheader()
    for r in sorted(billing_rows, key=lambda r: r["invoice_id"]):
        w.writerow(r)

with open("erp_ledger_export.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["invoice_id", "customer", "amount_eur", "booked_date", "period"])
    w.writeheader()
    for r in sorted(erp_rows, key=lambda r: r["invoice_id"]):
        w.writerow(r)

with open("contracts.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["customer", "monthly_contract_value_eur"])
    w.writeheader()
    for customer, value in CONTRACTS.items():
        w.writerow({"customer": customer, "monthly_contract_value_eur": value})

with open("planted_discrepancies_ANSWER_KEY.txt", "w") as f:
    f.write("Answer key -- for validating the reconciliation engine only, not part of the demo deliverable.\n\n")
    f.write("\n\n".join(planted))

print(f"Billing rows: {len(billing_rows)}")
print(f"ERP rows: {len(erp_rows)}")
print(f"Planted discrepancies: {len(planted)}")
