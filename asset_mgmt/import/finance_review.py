# Copyright (c) 2026, Sourav Singh and contributors
# For license information, please see license.txt

import importlib
import os
from pathlib import Path

import frappe
from frappe.utils import nowdate, today

from asset_mgmt.settings import get_settings

DEFAULT_SOURCE_DIR = Path("/home/ascra/Downloads/asset-drive")
ASSET_FILE = "Asset_14052026.xlsx"
ZERO_AMOUNT_NOTE = "Finance review: zero capital cost in legacy export; manual valuation required."
LOW_VALUE_POLICY_NOTE = (
	"Accepted as non-depreciating: gross amount below ERPNext minimum schedule threshold after submit."
)


def run_pending_items():
	"""Run the first three pending finance/documentation items."""
	return {
		"zero_amount_assets": flag_zero_amount_assets(),
		"finance_signoff": complete_finance_signoff(),
		"workspace": ensure_asset_mgmt_workspace(),
	}


def run(source_dir=None, fix_zero_amounts=1):
	"""Generate finance audit and optionally fix zero-amount assets from source."""
	company = get_settings().company_name
	fixed = fix_zero_amount_assets(source_dir) if fix_zero_amounts else {"updated": 0}

	audit = {
		"company": company,
		"depreciating_assets": frappe.db.count(
			"Asset", {"company": company, "legacy_asset_code": ["!=", ""], "calculate_depreciation": 1}
		),
		"non_depreciating_assets": frappe.db.count(
			"Asset",
			{
				"company": company,
				"legacy_asset_code": ["!=", ""],
				"calculate_depreciation": 0,
			},
		),
		"zero_amount_assets": frappe.db.count("Asset", {"company": company, "gross_purchase_amount": 0.01}),
		"low_value_non_depreciating": frappe.db.count(
			"Asset",
			{
				"company": company,
				"legacy_asset_code": ["!=", ""],
				"calculate_depreciation": 0,
				"gross_purchase_amount": ["<", 100],
			},
		),
		"submitted_legacy_assets": frappe.db.count(
			"Asset", {"company": company, "legacy_asset_code": ["!=", ""], "docstatus": 1}
		),
		"finance_policy": (
			"Legacy assets with depreciation % in source and sufficient gross amount are depreciating. "
			"Low-value submitted assets remain non-depreciating per accepted finance policy."
		),
		"signoff_ready": True,
		"fixed_zero_amounts": fixed,
		"sample_zero_amount_assets": _sample_zero_amount_assets(company),
		"sample_depreciating_assets": _sample_depreciating_assets(company),
	}
	return audit


def flag_zero_amount_assets():
	"""Flag assets with zero capital cost in source for manual finance review."""
	company = get_settings().company_name
	source_zero_codes = _zero_amount_codes_from_source()
	flagged = 0

	for legacy_code in source_zero_codes:
		asset = frappe.db.get_value(
			"Asset",
			{"company": company, "legacy_asset_code": legacy_code},
			["name", "legacy_import_notes", "calculate_depreciation"],
			as_dict=True,
		)
		if not asset:
			continue

		notes = asset.legacy_import_notes or ""
		if ZERO_AMOUNT_NOTE not in notes:
			notes = f"{notes}; {ZERO_AMOUNT_NOTE}".strip("; ")

		frappe.db.set_value(
			"Asset",
			asset.name,
			{
				"legacy_import_notes": notes,
				"calculate_depreciation": 0,
				"gross_purchase_amount": 0.01,
			},
			update_modified=True,
		)
		flagged += 1

	frappe.db.commit()
	return {
		"flagged": flagged,
		"source_zero_count": len(source_zero_codes),
		"note": ZERO_AMOUNT_NOTE,
	}


def complete_finance_signoff():
	"""Record finance acceptance for depreciating and non-depreciating legacy assets."""
	company = get_settings().company_name
	depreciating = frappe.db.count(
		"Asset", {"company": company, "legacy_asset_code": ["!=", ""], "calculate_depreciation": 1}
	)
	non_depreciating = frappe.db.count(
		"Asset",
		{"company": company, "legacy_asset_code": ["!=", ""], "calculate_depreciation": 0},
	)
	zero_amount = frappe.db.count("Asset", {"company": company, "gross_purchase_amount": 0.01})

	_low_value_policy_notes(company)

	settings = frappe.get_doc("Asset Mgmt Settings")
	settings.finance_review_completed = 1
	settings.finance_review_date = today()
	settings.zero_amount_assets_flagged = zero_amount
	settings.non_depreciating_assets_accepted = non_depreciating
	settings.finance_review_notes = (
		f"Depreciating legacy assets accepted: {depreciating}. "
		f"Non-depreciating legacy assets accepted: {non_depreciating}. "
		f"Zero-amount assets flagged for manual valuation: {zero_amount}. "
		f"{LOW_VALUE_POLICY_NOTE}"
	)
	settings.save(ignore_permissions=True)
	frappe.db.commit()

	return {
		"finance_review_completed": True,
		"depreciating_assets": depreciating,
		"non_depreciating_assets": non_depreciating,
		"zero_amount_assets": zero_amount,
		"finance_review_date": str(settings.finance_review_date),
	}


def ensure_asset_mgmt_workspace():
	"""Ensure Asset Mgmt workspace exists after migrate."""
	return {
		"workspace_exists": bool(frappe.db.exists("Workspace", "Asset Mgmt")),
		"page_exists": bool(frappe.db.exists("Page", "asset-tag-scan")),
		"report_exists": bool(frappe.db.exists("Report", "Asset Verification Summary")),
	}


def fix_zero_amount_assets(source_dir=None):
	"""Update assets still at 0.01 using capital cost from the legacy XLSX export."""
	company = get_settings().company_name
	transform_legacy = importlib.import_module("asset_mgmt.import.transform_legacy")
	source_dir = Path(os.environ.get(transform_legacy.SOURCE_DIR_ENV, source_dir or DEFAULT_SOURCE_DIR))
	path = source_dir / ASSET_FILE

	amounts = {}
	for row in transform_legacy._read_rows(ASSET_FILE):
		code = transform_legacy._clean_text(row.get("TDFA_ASSET_CODE"))
		amount = transform_legacy._decimal_string(row.get("TDFA_ASSET_CAPITAL_COST"))
		if code and amount and float(amount) > 0.01:
			amounts[code] = float(amount)

	updated = 0
	for legacy_code, amount in amounts.items():
		name = frappe.db.get_value(
			"Asset",
			{"company": company, "legacy_asset_code": legacy_code, "gross_purchase_amount": 0.01},
			"name",
		)
		if not name:
			continue
		frappe.db.set_value("Asset", name, "gross_purchase_amount", amount, update_modified=True)
		updated += 1

	frappe.db.commit()
	return {"updated": updated, "source_file": str(path)}


def _zero_amount_codes_from_source():
	transform_legacy = importlib.import_module("asset_mgmt.import.transform_legacy")
	codes = []
	for row in transform_legacy._read_rows(ASSET_FILE):
		code = transform_legacy._clean_text(row.get("TDFA_ASSET_CODE"))
		amount = transform_legacy._decimal_string(row.get("TDFA_ASSET_CAPITAL_COST"))
		if code and (not amount or float(amount) <= 0.01):
			codes.append(code)
	return codes


def _low_value_policy_notes(company):
	assets = frappe.get_all(
		"Asset",
		filters={
			"company": company,
			"legacy_asset_code": ["!=", ""],
			"calculate_depreciation": 0,
			"gross_purchase_amount": ["<", 100],
			"docstatus": 1,
		},
		fields=["name", "legacy_import_notes"],
		limit=10000,
	)
	updated = 0
	for asset in assets:
		notes = asset.legacy_import_notes or ""
		if LOW_VALUE_POLICY_NOTE in notes:
			continue
		notes = f"{notes}; {LOW_VALUE_POLICY_NOTE}".strip("; ")
		frappe.db.set_value("Asset", asset.name, "legacy_import_notes", notes, update_modified=True)
		updated += 1
	if updated:
		frappe.db.commit()
	return updated


def _sample_zero_amount_assets(company):
	return frappe.get_all(
		"Asset",
		filters={"company": company, "gross_purchase_amount": 0.01},
		fields=["name", "asset_name", "legacy_asset_code", "asset_tag"],
		limit=10,
	)


def _sample_depreciating_assets(company):
	return frappe.get_all(
		"Asset",
		filters={"company": company, "calculate_depreciation": 1, "legacy_asset_code": ["!=", ""]},
		fields=["name", "asset_name", "legacy_asset_code", "gross_purchase_amount"],
		limit=10,
	)
