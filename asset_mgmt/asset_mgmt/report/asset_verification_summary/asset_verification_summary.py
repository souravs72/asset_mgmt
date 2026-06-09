# Copyright (c) 2026, Sourav Singh and contributors
# For license information, please see license.txt

import frappe
from frappe import _

from asset_mgmt.settings import get_settings


def execute(filters=None):
	filters = frappe._dict(filters or {})
	columns = get_columns()
	data = get_data(filters)
	return columns, data


def get_columns():
	return [
		{"label": _("Verification"), "fieldname": "verification", "fieldtype": "Link", "options": "Asset Verification", "width": 150},
		{"label": _("Location"), "fieldname": "location", "fieldtype": "Link", "options": "Location", "width": 150},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 100},
		{"label": _("Asset"), "fieldname": "asset", "fieldtype": "Link", "options": "Asset", "width": 140},
		{"label": _("Asset Tag"), "fieldname": "asset_tag", "fieldtype": "Data", "width": 120},
		{"label": _("Expected Location"), "fieldname": "expected_location", "fieldtype": "Link", "options": "Location", "width": 140},
		{"label": _("Scanned Location"), "fieldname": "scanned_location", "fieldtype": "Link", "options": "Location", "width": 140},
		{"label": _("Expected Custodian"), "fieldname": "expected_custodian", "fieldtype": "Link", "options": "Employee", "width": 140},
		{"label": _("Scanned Custodian"), "fieldname": "scanned_custodian", "fieldtype": "Link", "options": "Employee", "width": 140},
		{"label": _("Result"), "fieldname": "verification_result", "fieldtype": "Data", "width": 130},
	]


def get_data(filters):
	company = filters.company or get_settings().company_name
	conditions = ["av.company = %s"]
	values = [company]

	if filters.verification:
		conditions.append("av.name = %s")
		values.append(filters.verification)
	if filters.location:
		conditions.append("av.location = %s")
		values.append(filters.location)
	if filters.verification_result:
		conditions.append("avi.verification_result = %s")
		values.append(filters.verification_result)
	if filters.show_mismatches_only:
		conditions.append(
			"avi.verification_result IN ('Location Mismatch', 'Custodian Mismatch', 'Not Found', 'Extra')"
		)
	if filters.show_scanned_only:
		conditions.append("IFNULL(avi.scanned_location, '') != ''")

	where = " AND ".join(conditions)
	return frappe.db.sql(
		f"""
		SELECT
			av.name AS verification,
			av.location,
			av.status,
			avi.asset,
			a.asset_tag,
			avi.expected_location,
			avi.scanned_location,
			avi.expected_custodian,
			avi.scanned_custodian,
			avi.verification_result
		FROM `tabAsset Verification Item` avi
		INNER JOIN `tabAsset Verification` av ON av.name = avi.parent
		LEFT JOIN `tabAsset` a ON a.name = avi.asset
		WHERE {where}
		ORDER BY av.location, avi.verification_result, a.asset_tag
		""",
		values,
		as_dict=True,
	)
