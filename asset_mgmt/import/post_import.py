"""Validate legacy import, fix data, submit assets, and seed verification records.

Examples:
bench --site SITE execute asset_mgmt.import.post_import.run_all
bench --site SITE execute asset_mgmt.import.post_import.validate_sync
bench --site SITE execute asset_mgmt.import.post_import.fix_data_quality --kwargs '{"csv_dir": "/tmp/asset-import-check"}'
bench --site SITE execute asset_mgmt.import.post_import.submit_legacy_assets --kwargs '{"batch_size": 200}'
bench --site SITE execute asset_mgmt.import.post_import.create_location_verifications
"""

from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Any

import frappe
from frappe.utils import getdate, nowdate, today

from asset_mgmt.settings import get_company, get_settings

DEFAULT_CSV_DIR = "/tmp/asset-import-check"
LEGACY_IN_SERVICE_DATE = "2019-04-01"


def run_all(csv_dir=DEFAULT_CSV_DIR, batch_size=200, submit_verifications=False):
	"""Run validation, data fixes, finance policy, asset submit, and verification seed."""
	validation = validate_sync(csv_dir)
	fixes = fix_data_quality(csv_dir)
	cost_centers = fix_cost_centers()
	dates = fix_legacy_dates()
	finance = apply_finance_policy(csv_dir)
	submit = submit_legacy_assets(batch_size=batch_size)
	verification = create_location_verifications(submit_verifications=submit_verifications)
	return {
		"validation": validation,
		"fixes": fixes,
		"cost_centers": cost_centers,
		"dates": dates,
		"finance": finance,
		"submit": submit,
		"verification": verification,
	}


def validate_sync(csv_dir=DEFAULT_CSV_DIR):
	"""Compare source XLSX, generated CSV, and ERPNext database counts."""
	import importlib

	transform_legacy = importlib.import_module("asset_mgmt.import.transform_legacy")
	ASSET_FILE = transform_legacy.ASSET_FILE
	CATEGORY_FILE = transform_legacy.CATEGORY_FILE
	COST_CENTER_FILE = transform_legacy.COST_CENTER_FILE
	LOCATION_FILE = transform_legacy.LOCATION_FILE
	SUPPLIER_FILE = transform_legacy.SUPPLIER_FILE
	_read_rows = transform_legacy._read_rows

	company = get_company()
	csv_path = Path(os.path.expanduser(csv_dir))

	def csv_count(name: str) -> int:
		path = csv_path / name
		if not path.exists():
			return -1
		with path.open(newline="", encoding="utf-8") as handle:
			return sum(1 for _ in csv.DictReader(handle))

	def xlsx_count(file_name: str) -> int:
		return sum(1 for _ in _read_rows(file_name))

	report = {
		"company": company,
		"source_vs_csv": {
			"assets": {"xlsx": xlsx_count(ASSET_FILE), "csv": csv_count("asset.csv")},
			"locations": {"xlsx": xlsx_count(LOCATION_FILE), "csv": csv_count("location.csv")},
			"cost_centers": {"xlsx": xlsx_count(COST_CENTER_FILE), "csv": csv_count("cost_center.csv")},
			"suppliers": {"xlsx": xlsx_count(SUPPLIER_FILE), "csv": csv_count("supplier.csv")},
		},
		"database": {
			"assets_total": frappe.db.count("Asset", {"company": company}),
			"assets_legacy": frappe.db.count("Asset", {"company": company, "legacy_asset_code": ["!=", ""]}),
			"assets_draft": frappe.db.count(
				"Asset", {"company": company, "legacy_asset_code": ["!=", ""], "docstatus": 0}
			),
			"assets_submitted": frappe.db.count(
				"Asset", {"company": company, "legacy_asset_code": ["!=", ""], "docstatus": 1}
			),
			"locations": frappe.db.count("Location"),
			"cost_centers": frappe.db.count("Cost Center", {"company": company}),
			"suppliers": frappe.db.count("Supplier"),
			"asset_verifications": frappe.db.count("Asset Verification", {"company": company}),
		},
		"quality": _quality_snapshot(company),
	}
	report["in_sync"] = (
		report["source_vs_csv"]["assets"]["xlsx"] == report["database"]["assets_legacy"]
		and report["source_vs_csv"]["assets"]["csv"] == report["database"]["assets_legacy"]
	)
	return report


def fix_data_quality(csv_dir=DEFAULT_CSV_DIR):
	"""Update legacy Asset records from generated CSV (amounts, suppliers, serials, notes)."""
	company = get_company()
	rows = _load_asset_csv(csv_dir)
	updated = skipped = failed = 0
	errors: list[str] = []

	for row in rows:
		code = row.get("legacy_asset_code") or row.get("asset_tag")
		if not code:
			continue

		asset_name = frappe.db.get_value(
			"Asset", {"company": company, "legacy_asset_code": code}, "name"
		)
		if not asset_name:
			skipped += 1
			continue

		updates = _build_asset_updates(row)
		if not updates:
			skipped += 1
			continue

		if updates.get("cost_center"):
			updates["cost_center"] = _resolve_cost_center(updates["cost_center"], company)

		try:
			frappe.db.set_value("Asset", asset_name, updates, update_modified=True)
			updated += 1
			if updated % 500 == 0:
				frappe.db.commit()
		except Exception as exc:
			failed += 1
			if len(errors) < 5:
				errors.append(f"{code}: {exc}")

	frappe.db.commit()
	return {"updated": updated, "skipped": skipped, "failed": failed, "errors": errors}


def fix_legacy_dates():
	"""Move placeholder legacy dates into the first active fiscal year."""
	company = get_company()
	cutoff = getdate(LEGACY_IN_SERVICE_DATE)
	assets = frappe.get_all(
		"Asset",
		filters={"company": company, "legacy_asset_code": ["!=", ""]},
		fields=["name", "purchase_date", "available_for_use_date"],
	)
	updated = 0
	for asset in assets:
		purchase = getdate(asset.purchase_date) if asset.purchase_date else None
		available = getdate(asset.available_for_use_date) if asset.available_for_use_date else None
		if purchase and purchase >= cutoff and available and available >= cutoff:
			continue
		frappe.db.set_value(
			"Asset",
			asset.name,
			{
				"purchase_date": cutoff,
				"available_for_use_date": cutoff,
			},
			update_modified=True,
		)
		updated += 1
		if updated % 500 == 0:
			frappe.db.commit()
	frappe.db.commit()
	frappe.db.sql(
		"""
		UPDATE `tabAsset Finance Book` afb
		INNER JOIN `tabAsset` a ON afb.parent = a.name
		SET afb.depreciation_start_date = %s
		WHERE a.company = %s
		  AND IFNULL(a.legacy_asset_code, '') != ''
		  AND afb.depreciation_start_date < %s
		""",
		(cutoff, company, cutoff),
	)
	frappe.db.commit()
	return {"updated": updated, "legacy_in_service_date": LEGACY_IN_SERVICE_DATE}


def fix_cost_centers():
	"""Resolve legacy asset cost center links to suffixed Cost Center names."""
	company = get_company()
	assets = frappe.get_all(
		"Asset",
		filters={"company": company, "legacy_asset_code": ["!=", ""]},
		fields=["name", "cost_center"],
	)
	updated = skipped = 0
	for asset in assets:
		if not asset.cost_center:
			skipped += 1
			continue
		resolved = _resolve_cost_center(asset.cost_center, company)
		if resolved == asset.cost_center:
			skipped += 1
			continue
		frappe.db.set_value("Asset", asset.name, "cost_center", resolved, update_modified=True)
		updated += 1
		if updated % 500 == 0:
			frappe.db.commit()
	frappe.db.commit()
	return {"updated": updated, "skipped": skipped}


def apply_finance_policy(csv_dir=DEFAULT_CSV_DIR):
	"""Enable depreciation for legacy assets where source data includes depreciation %."""
	company = get_company()
	rows = _load_asset_csv(csv_dir)
	enabled = skipped = failed = 0
	errors: list[str] = []

	for row in rows:
		code = row.get("legacy_asset_code") or row.get("asset_tag")
		if not code:
			continue

		asset = frappe.db.get_value(
			"Asset",
			{"company": company, "legacy_asset_code": code},
			["name", "calculate_depreciation", "gross_purchase_amount"],
			as_dict=True,
		)
		if not asset:
			continue

		updates: dict[str, Any] = {}
		amount = float(row.get("gross_purchase_amount") or 0)
		if amount <= 0.01:
			amount = 0.01
			updates["gross_purchase_amount"] = amount

		if row.get("calculate_depreciation") in {"1", 1, True, "True"}:
			updates["calculate_depreciation"] = 1
			if row.get("opening_accumulated_depreciation"):
				updates["opening_accumulated_depreciation"] = float(row["opening_accumulated_depreciation"])

		if not updates:
			skipped += 1
			continue

		try:
			doc = frappe.get_doc("Asset", asset.name)
			if doc.cost_center:
				doc.cost_center = _resolve_cost_center(doc.cost_center, company)
			if getdate(doc.available_for_use_date or doc.purchase_date or today()) < getdate(
				LEGACY_IN_SERVICE_DATE
			):
				doc.purchase_date = LEGACY_IN_SERVICE_DATE
				doc.available_for_use_date = LEGACY_IN_SERVICE_DATE
			doc.update(updates)
			if updates.get("calculate_depreciation"):
				if doc.finance_books:
					for book in doc.finance_books:
						book.depreciation_start_date = doc.available_for_use_date or doc.purchase_date
				else:
					doc.append(
						"finance_books",
						{
							"depreciation_method": "Straight Line",
							"total_number_of_depreciations": 60,
							"frequency_of_depreciation": 12,
							"depreciation_start_date": doc.available_for_use_date or doc.purchase_date,
						},
					)
			doc.save(ignore_permissions=True)
			enabled += 1
			if enabled % 200 == 0:
				frappe.db.commit()
		except Exception as exc:
			failed += 1
			if len(errors) < 5:
				errors.append(f"{code}: {exc}")

	frappe.db.commit()
	return {
		"finance_policy": "Enable depreciation when legacy source has depreciation %; minimum amount 0.01",
		"updated": enabled,
		"skipped": skipped,
		"failed": failed,
		"errors": errors,
		"after": _quality_snapshot(company),
	}


def submit_legacy_assets(batch_size=200):
	"""Submit draft legacy assets in batches."""
	company = get_company()
	names = frappe.get_all(
		"Asset",
		filters={"company": company, "legacy_asset_code": ["!=", ""], "docstatus": 0},
		pluck="name",
		order_by="creation asc",
	)

	submitted = failed = 0
	errors: list[str] = []

	for index, name in enumerate(names, start=1):
		try:
			doc = frappe.get_doc("Asset", name)
			if doc.docstatus != 0:
				continue
			if not doc.gross_purchase_amount or float(doc.gross_purchase_amount) <= 0:
				doc.gross_purchase_amount = 0.01
				doc.save(ignore_permissions=True)
			doc.submit()
			submitted += 1
			if submitted % batch_size == 0:
				frappe.db.commit()
		except Exception as exc:
			frappe.db.rollback()
			failed += 1
			if len(errors) < 10:
				errors.append(f"{name}: {exc}")

	frappe.db.commit()
	return {
		"attempted": len(names),
		"submitted": submitted,
		"failed": failed,
		"errors": errors,
		"remaining_draft": frappe.db.count(
			"Asset", {"company": company, "legacy_asset_code": ["!=", ""], "docstatus": 0}
		),
	}


def create_location_verifications(submit_verifications=False):
	"""Create baseline Asset Verification records grouped by location."""
	company = get_company()
	locations = frappe.db.sql(
		"""
		SELECT location, COUNT(*) AS asset_count
		FROM `tabAsset`
		WHERE company = %s
		  AND docstatus = 1
		  AND IFNULL(location, '') != ''
		  AND IFNULL(legacy_asset_code, '') != ''
		GROUP BY location
		ORDER BY asset_count DESC
		""",
		company,
		as_dict=True,
	)

	created = skipped = failed = 0
	errors: list[str] = []

	for row in locations:
		location = row.location
		if frappe.db.exists(
			"Asset Verification",
			{"company": company, "location": location, "verification_date": today()},
		):
			skipped += 1
			continue

		assets = frappe.get_all(
			"Asset",
			filters={
				"company": company,
				"location": location,
				"docstatus": 1,
				"legacy_asset_code": ["!=", ""],
			},
			fields=["name", "asset_tag", "location", "custodian", "operational_status"],
			order_by="asset_tag asc",
		)
		if not assets:
			skipped += 1
			continue

		try:
			doc = frappe.get_doc(
				{
					"doctype": "Asset Verification",
					"company": company,
					"location": location,
					"verification_date": today(),
					"verified_by": frappe.session.user,
					"verify_only": 1,
					"update_assets_on_submit": 0,
					"items": [
						{
							"asset": asset.name,
							"scanned_location": asset.location,
							"scanned_custodian": asset.custodian,
							"operational_status": asset.operational_status,
						}
						for asset in assets
					],
				}
			)
			doc.insert(ignore_permissions=True)
			if submit_verifications:
				doc.submit()
			created += 1
			if created % 10 == 0:
				frappe.db.commit()
		except Exception as exc:
			frappe.db.rollback()
			failed += 1
			if len(errors) < 10:
				errors.append(f"{location}: {exc}")

	frappe.db.commit()
	return {
		"locations_processed": len(locations),
		"created": created,
		"skipped": skipped,
		"failed": failed,
		"submitted": bool(submit_verifications),
		"errors": errors,
		"total_verifications": frappe.db.count("Asset Verification", {"company": company}),
	}


def _resolve_cost_center(cost_center: str, company: str) -> str:
	if not cost_center:
		return cost_center
	if frappe.db.exists("Cost Center", cost_center):
		return cost_center

	abbr = get_settings().company_abbr
	suffixed = f"{cost_center} - {abbr}"
	if frappe.db.exists("Cost Center", suffixed):
		return suffixed

	match = frappe.db.get_value(
		"Cost Center",
		{"cost_center_name": cost_center, "company": company},
		"name",
	)
	return match or cost_center


def _load_asset_csv(csv_dir: str) -> list[dict[str, str]]:
	path = Path(os.path.expanduser(csv_dir)) / "asset.csv"
	if not path.exists():
		raise FileNotFoundError(f"Asset CSV not found: {path}")
	with path.open(newline="", encoding="utf-8") as handle:
		return list(csv.DictReader(handle))


def _build_asset_updates(row: dict[str, str]) -> dict[str, Any]:
	updates: dict[str, Any] = {}
	mapping = {
		"gross_purchase_amount": "gross_purchase_amount",
		"supplier": "supplier",
		"serial_number": "serial_number",
		"legacy_group": "legacy_group",
		"legacy_category": "legacy_category",
		"legacy_subcategory": "legacy_subcategory",
		"asset_condition": "asset_condition",
		"legacy_import_notes": "legacy_import_notes",
		"operational_status": "operational_status",
		"cost_center": "cost_center",
		"purchase_date": "purchase_date",
		"available_for_use_date": "available_for_use_date",
	}

	for source, target in mapping.items():
		value = row.get(source)
		if value not in (None, ""):
			if target in {"purchase_date", "available_for_use_date"}:
				updates[target] = getdate(value)
			elif target == "gross_purchase_amount":
				amount = float(value)
				if amount > 0:
					updates[target] = amount
			else:
				updates[target] = value

	opening = row.get("opening_accumulated_depreciation")
	if opening not in (None, ""):
		updates["opening_accumulated_depreciation"] = float(opening)

	if updates.get("cost_center"):
		updates["cost_center"] = _resolve_cost_center(updates["cost_center"], get_company())

	return updates


def _quality_snapshot(company: str) -> dict[str, int]:
	return {
		"draft_legacy_assets": frappe.db.count(
			"Asset", {"company": company, "legacy_asset_code": ["!=", ""], "docstatus": 0}
		),
		"submitted_legacy_assets": frappe.db.count(
			"Asset", {"company": company, "legacy_asset_code": ["!=", ""], "docstatus": 1}
		),
		"assets_amount_0_01": frappe.db.count("Asset", {"company": company, "gross_purchase_amount": 0.01}),
		"assets_with_depreciation": frappe.db.count(
			"Asset", {"company": company, "legacy_asset_code": ["!=", ""], "calculate_depreciation": 1}
		),
		"assets_without_supplier": frappe.db.sql(
			"SELECT COUNT(*) FROM `tabAsset` WHERE company = %s AND IFNULL(supplier, '') = ''",
			company,
		)[0][0],
	}
