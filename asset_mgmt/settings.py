# Copyright (c) 2026, Sourav Singh and contributors
# For license information, please see license.txt

import frappe


def get_settings():
	"""Read configurable settings from Asset Mgmt Settings single DocType."""
	defaults = frappe._dict(
		{
			"enable_demo_setup": 1,
			"load_demo_data": 1,
			"company_name": "Asset Management",
			"company_abbr": "AM",
			"country": "India",
			"currency": "INR",
			"chart_of_accounts": "India - Chart of Accounts",
			"timezone": "Asia/Kolkata",
			"admin_email": "souravsingh2609@gmail.com",
			"admin_full_name": "Sourav Singh",
			"company_tagline": "Asset Management",
		}
	)

	if not frappe.db.table_exists("tabAsset Mgmt Settings"):
		return defaults

	if not frappe.db.exists("Asset Mgmt Settings", "Asset Mgmt Settings"):
		return defaults

	doc = frappe.get_cached_doc("Asset Mgmt Settings")
	for key in defaults:
		value = doc.get(key)
		if value not in (None, ""):
			defaults[key] = value

	return defaults


def ensure_default_settings():
	if not frappe.db.exists("Asset Mgmt Settings", "Asset Mgmt Settings"):
		doc = frappe.new_doc("Asset Mgmt Settings")
		doc.insert(ignore_permissions=True)
