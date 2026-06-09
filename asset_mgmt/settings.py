# Copyright (c) 2026, Sourav Singh and contributors
# For license information, please see license.txt

import frappe


def get_settings():
	"""Read configurable settings from Asset Mgmt Settings single DocType."""
	defaults = frappe._dict(
		{
			"enable_demo_setup": 0,
			"load_demo_data": 0,
			"finalize_site_on_install": 0,
			"company_name": "",
			"company_abbr": "",
			"country": "",
			"currency": "",
			"chart_of_accounts": "",
			"timezone": "",
			"admin_email": "",
			"admin_full_name": "",
			"company_tagline": "",
		}
	)

	if not frappe.db.count("Singles", {"doctype": "Asset Mgmt Settings"}):
		return _resolve_company_defaults(defaults)

	doc = frappe.get_cached_doc("Asset Mgmt Settings")
	for key in defaults:
		value = doc.get(key)
		if value not in (None, ""):
			defaults[key] = value

	return _resolve_company_defaults(defaults)


def _resolve_company_defaults(settings):
	"""Use the site's configured ERPNext company when demo setup is off."""
	if settings.enable_demo_setup:
		return settings

	if not settings.company_name:
		settings.company_name = frappe.defaults.get_global_default("company") or ""

	if settings.company_name and not settings.company_abbr:
		settings.company_abbr = frappe.db.get_value("Company", settings.company_name, "abbr") or ""

	return settings


def get_company():
	"""Return the active company, raising if the site is not configured yet."""
	settings = get_settings()
	if not settings.company_name:
		frappe.throw(
			"No company configured. Complete the ERPNext setup wizard first.",
			title="Asset Mgmt",
		)
	return settings.company_name


DEMO_SETUP_FIELDS = {
	"company_name": "Company Name",
	"company_abbr": "Company Abbreviation",
	"country": "Country",
	"currency": "Currency",
	"chart_of_accounts": "Chart of Accounts Template",
	"timezone": "Timezone",
	"admin_full_name": "Admin Full Name",
	"admin_email": "Admin Email",
}


def validate_demo_settings(settings):
	missing = [label for field, label in DEMO_SETUP_FIELDS.items() if not settings.get(field)]
	if missing:
		frappe.throw(
			"Demo setup requires: " + ", ".join(missing),
			title="Asset Mgmt Settings",
		)


def ensure_default_settings():
	if frappe.db.count("Singles", {"doctype": "Asset Mgmt Settings"}):
		return

	frappe.new_doc("Asset Mgmt Settings").insert(ignore_permissions=True)
