# Copyright (c) 2026, Sourav Singh and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import add_months, getdate, nowdate

from asset_mgmt.settings import ensure_default_settings, get_settings


def setup_asset_management():
	"""Full Phase 1-4 demo setup driven by Asset Mgmt Settings."""
	from asset_mgmt.install import sync_custom_fields

	sync_custom_fields()
	ensure_default_settings()
	settings = get_settings()

	if not settings.enable_demo_setup:
		if settings.finalize_site_on_install:
			finalize_site_setup(settings)
		return

	company = settings.company_name
	if frappe.db.exists("Company", company):
		frappe.msgprint(f"Company {company} already exists — running master/asset seed only.")
	else:
		_setup_company(settings)

	_configure_company_accounts(company, settings)
	_ensure_fiscal_years()
	_create_locations()
	categories = _create_asset_categories(company, settings)
	_create_items(company, categories)
	employees = _create_employees(company)

	if settings.load_demo_data:
		_create_assets(company, employees, settings)
		_backfill_asset_tags(company)
		_create_asset_movements(company)

	if settings.finalize_site_on_install:
		finalize_site_setup(settings)

	frappe.db.commit()


def finalize_site_setup(settings=None):
	"""Mark setup wizard complete and set global defaults (fixes desk/setup-wizard redirect loop)."""
	from frappe.desk.page.setup_wizard.setup_wizard import disable_future_access

	settings = settings or get_settings()
	company = settings.company_name
	if not frappe.db.exists("Company", company):
		return

	# Global Defaults — required for frappe.sys_defaults.company
	global_defaults = frappe.get_doc("Global Defaults")
	global_defaults.default_company = company
	global_defaults.default_currency = frappe.db.get_value("Company", company, "default_currency") or "INR"
	global_defaults.country = frappe.db.get_value("Company", company, "country") or "India"
	global_defaults.save(ignore_permissions=True)

	# System Settings — country/currency/timezone used by boot
	system_settings = frappe.get_doc("System Settings")
	if not system_settings.country:
		system_settings.update(
			{
				"country": "India",
				"currency": "INR",
				"language": "en",
				"time_zone": "Asia/Kolkata",
				"enable_scheduler": 1,
			}
		)
		system_settings.save(ignore_permissions=True)

	# User / session defaults
	frappe.db.set_default("company", company)
	frappe.db.set_default("currency", global_defaults.default_currency)

	# Mark every installed app as setup-complete (stops wizard re-entry for HRMS etc.)
	for app in frappe.get_installed_apps():
		frappe.db.set_value("Installed Application", {"app_name": app}, "is_setup_complete", 1)

	disable_future_access()
	frappe.db.set_single_value("System Settings", "enable_onboarding", 0)
	frappe.db.set_single_value("System Settings", "setup_complete", 1)
	frappe.clear_cache()



def _setup_company(settings):
	from erpnext.setup.setup_wizard.setup_wizard import setup_complete

	current_year = getdate().year
	setup_complete(
		frappe._dict(
			{
				"currency": settings.currency,
				"full_name": settings.admin_full_name,
				"company_name": settings.company_name,
				"timezone": settings.timezone,
				"company_abbr": settings.company_abbr,
				"industry": "Services",
				"country": settings.country,
				"fy_start_date": f"{current_year}-04-01",
				"fy_end_date": f"{current_year + 1}-03-31",
				"language": "english",
				"company_tagline": settings.company_tagline,
				"email": settings.admin_email,
				"password": "admin",
				"chart_of_accounts": settings.chart_of_accounts,
				"domain": "Services",
			}
		)
	)


def _get_account(name_suffix, settings):
	"""Return account name for configured company."""
	return f"{name_suffix} - {settings.company_abbr}"


def _configure_company_accounts(company, settings):
	company_doc = frappe.get_doc("Company", company)
	company_doc.accumulated_depreciation_account = _get_account("Accumulated Depreciations", settings)
	company_doc.depreciation_expense_account = _get_account("Depreciation", settings)
	company_doc.disposal_account = _get_account("Gain/Loss on Asset Disposal", settings)
	cost_center = frappe.db.get_value("Cost Center", {"company": company, "is_group": 0})
	if cost_center:
		company_doc.depreciation_cost_center = cost_center
	company_doc.save(ignore_permissions=True)

	frappe.db.set_single_value("Accounts Settings", "book_asset_depreciation_entry_automatically", 1)


def _create_location(name, parent=None, is_group=0):
	if frappe.db.exists("Location", name):
		return name

	doc = frappe.get_doc(
		{
			"doctype": "Location",
			"location_name": name,
			"parent_location": parent,
			"is_group": is_group,
		}
	)
	doc.insert(ignore_permissions=True)
	return name


def _ensure_fiscal_years():
	start_year = 2019
	end_year = 2027
	for year in range(start_year, end_year + 1):
		name = f"{year}-{year + 1}"
		if frappe.db.exists("Fiscal Year", name):
			continue
		frappe.get_doc(
			{
				"doctype": "Fiscal Year",
				"year": name,
				"year_start_date": f"{year}-04-01",
				"year_end_date": f"{year + 1}-03-31",
			}
		).insert(ignore_permissions=True)


def _create_locations():
	_create_location("Head Office", is_group=1)
	_create_location("Floor 1", parent="Head Office", is_group=1)
	_create_location("Floor 2", parent="Head Office", is_group=1)
	for loc in ("Reception", "IT Room"):
		_create_location(loc, parent="Floor 1")
	for loc in ("Conference Room", "Open Office"):
		_create_location(loc, parent="Floor 2")


def _create_asset_category(name, fixed_asset_account, settings, finance_books=None):
	if frappe.db.exists("Asset Category", name):
		return name

	doc = frappe.get_doc(
		{
			"doctype": "Asset Category",
			"asset_category_name": name,
			"enable_cwip_accounting": 0,
			"accounts": [
				{
					"company_name": settings.company_name,
					"fixed_asset_account": _get_account(fixed_asset_account, settings),
					"accumulated_depreciation_account": _get_account("Accumulated Depreciations", settings),
					"depreciation_expense_account": _get_account("Depreciation", settings),
				}
			],
		}
	)

	if finance_books:
		for fb in finance_books:
			doc.append("finance_books", fb)

	doc.insert(ignore_permissions=True)
	return name


def _create_asset_categories(company, settings):
	categories = {}
	for name, account in (
		("IT Equipment", "Electronic Equipments"),
		("Office Furniture", "Furnitures and Fixtures"),
		("Vehicles", "Capital Equipments"),
	):
		categories[name] = _create_asset_category(
			name,
			account,
			settings,
			finance_books=[
				{
					"depreciation_method": "Straight Line",
					"total_number_of_depreciations": 36 if name == "IT Equipment" else 60,
					"frequency_of_depreciation": 1,
				}
			],
		)
	return categories


def _create_item(item_code, item_name, asset_category):
	if frappe.db.exists("Item", item_code):
		return item_code

	naming_series = frappe.get_meta("Asset").get_field("naming_series").options.split("\n")[0]
	frappe.get_doc(
		{
			"doctype": "Item",
			"item_code": item_code,
			"item_name": item_name,
			"item_group": "All Item Groups",
			"stock_uom": "Nos",
			"is_fixed_asset": 1,
			"is_stock_item": 0,
			"asset_category": asset_category,
			"auto_create_assets": 0,
			"asset_naming_series": naming_series,
		}
	).insert(ignore_permissions=True)
	return item_code


def _create_items(company, categories):
	items = {
		"LAPTOP-001": ("Laptop", "IT Equipment"),
		"MONITOR-001": ("Monitor", "IT Equipment"),
		"DESK-001": ("Office Desk", "Office Furniture"),
		"CHAIR-001": ("Office Chair", "Office Furniture"),
		"VEHICLE-001": ("Company Vehicle", "Vehicles"),
	}
	for code, (name, category) in items.items():
		_create_item(code, name, categories[category])


def _ensure_genders():
	for gender in ("Male", "Female", "Other"):
		if not frappe.db.exists("Gender", gender):
			frappe.get_doc({"doctype": "Gender", "gender": gender}).insert(ignore_permissions=True)


def _create_user_and_employee(email, first_name, company):
	_ensure_genders()
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": first_name,
				"send_welcome_email": 0,
				"roles": [{"role": "Employee"}],
			}
		).insert(ignore_permissions=True)

	if frappe.db.exists("Employee", {"company_email": email}):
		return frappe.db.get_value("Employee", {"company_email": email})

	department = frappe.db.get_value("Department", {"company": company})
	employee = frappe.get_doc(
		{
			"doctype": "Employee",
			"first_name": first_name,
			"company": company,
			"company_email": email,
			"prefered_contact_email": "Company Email",
			"prefered_email": email,
			"date_of_birth": "1990-01-15",
			"date_of_joining": "2020-06-01",
			"gender": "Male",
			"status": "Active",
			"department": department,
		}
	)
	employee.insert(ignore_permissions=True)
	return employee.name


def _create_employees(company):
	employees = []
	for email, name in (
		("amit.kumar@assetmgmt.local", "Amit Kumar"),
		("priya.sharma@assetmgmt.local", "Priya Sharma"),
		("rahul.verma@assetmgmt.local", "Rahul Verma"),
		("sneha.patel@assetmgmt.local", "Sneha Patel"),
	):
		employees.append(_create_user_and_employee(email, name, company))
	return employees


def _create_assets(company, employees, settings=None):
	locations = ["IT Room", "Reception", "Open Office", "Conference Room"]
	purchase_dates = [
		"2023-06-15", "2023-05-01", "2023-04-10", "2023-08-20", "2023-07-12",
		"2023-09-05", "2023-10-01", "2022-11-01", "2022-10-15", "2022-09-20",
		"2022-08-05", "2022-07-18", "2022-06-25", "2022-05-30", "2021-03-01",
		"2020-11-15", "2023-03-22", "2023-02-14",
	]
	items = [
		("LAPTOP-001", "IT Equipment", 65000, 15000),
		("LAPTOP-001", "IT Equipment", 72000, 18000),
		("LAPTOP-001", "IT Equipment", 58000, 12000),
		("MONITOR-001", "IT Equipment", 18000, 4000),
		("MONITOR-001", "IT Equipment", 15000, 3000),
		("MONITOR-001", "IT Equipment", 22000, 5000),
		("MONITOR-001", "IT Equipment", 16000, 3500),
		("DESK-001", "Office Furniture", 12000, 2000),
		("DESK-001", "Office Furniture", 14000, 2500),
		("DESK-001", "Office Furniture", 11000, 1800),
		("CHAIR-001", "Office Furniture", 8000, 1500),
		("CHAIR-001", "Office Furniture", 9500, 1800),
		("CHAIR-001", "Office Furniture", 7500, 1200),
		("CHAIR-001", "Office Furniture", 9000, 1600),
		("VEHICLE-001", "Vehicles", 850000, 200000),
		("VEHICLE-001", "Vehicles", 920000, 220000),
		("LAPTOP-001", "IT Equipment", 68000, 14000),
		("MONITOR-001", "IT Equipment", 19000, 4200),
	]

	tag_prefix = {"IT Equipment": "AST-IT", "Office Furniture": "AST-FN", "Vehicles": "AST-VH"}
	tag_counters = {k: 0 for k in tag_prefix}

	for idx, (item_code, category, amount, opening_depr) in enumerate(items):
		tag_counters[category] += 1
		tag = f"{tag_prefix[category]}-{tag_counters[category]:05d}"
		asset_name = f"{frappe.db.get_value('Item', item_code, 'item_name')} #{idx + 1}"
		location = locations[idx % len(locations)]
		custodian = employees[idx % len(employees)]
		operational_status = "Under Maintenance" if idx in (5, 12) else "Active"

		if frappe.db.exists("Asset", {"asset_name": asset_name, "company": company}):
			continue

		purchase_date = purchase_dates[idx]
		total_depr = 36 if category == "IT Equipment" else 60
		opening_depr_count = max(1, int(opening_depr / (amount / total_depr))) if opening_depr else 0
		depreciation_start = add_months(purchase_date, opening_depr_count or 1)
		asset = frappe.get_doc(
			{
				"doctype": "Asset",
				"asset_name": asset_name,
				"item_code": item_code,
				"asset_category": category,
				"company": company,
				"location": location,
				"custodian": custodian,
				"purchase_date": purchase_date,
				"available_for_use_date": purchase_date,
				"gross_purchase_amount": amount,
				"purchase_amount": amount,
				"is_existing_asset": 1,
				"calculate_depreciation": 1,
				"opening_accumulated_depreciation": opening_depr,
				"opening_number_of_booked_depreciations": opening_depr_count,
				"asset_tag": tag,
				"asset_tag_type": "Barcode",
				"operational_status": operational_status,
				"finance_books": [
					{
						"depreciation_method": "Straight Line",
						"total_number_of_depreciations": total_depr,
						"frequency_of_depreciation": 1,
						"depreciation_start_date": depreciation_start,
						"expected_value_after_useful_life": 0,
					}
				],
			}
		)
		asset.insert(ignore_permissions=True)
		asset.submit()


def _backfill_asset_tags(company):
	"""Set tags and operational status on assets created before custom fields existed."""
	tag_prefix = {"IT Equipment": "AST-IT", "Office Furniture": "AST-FN", "Vehicles": "AST-VH"}
	tag_counters = {k: 0 for k in tag_prefix}

	assets = frappe.get_all(
		"Asset",
		filters={"company": company},
		fields=["name", "asset_name", "asset_category", "asset_tag", "operational_status"],
		order_by="creation asc",
	)

	for idx, asset in enumerate(assets):
		tag_counters[asset.asset_category] = tag_counters.get(asset.asset_category, 0) + 1
		tag = f"{tag_prefix.get(asset.asset_category, 'AST')}-{tag_counters[asset.asset_category]:05d}"
		operational_status = "Under Maintenance" if idx in (5, 12) else "Active"

		if asset.asset_tag == tag and asset.operational_status == operational_status:
			continue

		frappe.db.set_value(
			"Asset",
			asset.name,
			{
				"asset_tag": tag,
				"asset_tag_type": "Barcode",
				"operational_status": operational_status,
			},
			update_modified=False,
		)


def _create_asset_movements(company):
	"""Create sample transfer and issue movements for audit trail demo."""
	assets = frappe.get_all("Asset", {"company": company, "docstatus": 1}, pluck="name")
	employees = frappe.get_all("Employee", {"company": company}, pluck="name")
	if len(assets) < 7 or len(employees) < 2:
		return

	movements = [
		{
			"purpose": "Transfer",
			"asset": assets[0],
			"source": frappe.db.get_value("Asset", assets[0], "location"),
			"target": "Open Office",
		},
		{
			"purpose": "Issue",
			"asset": assets[3],
			"from_employee": frappe.db.get_value("Asset", assets[3], "custodian"),
			"to_employee": employees[1],
		},
		{
			"purpose": "Transfer",
			"asset": assets[6],
			"source": frappe.db.get_value("Asset", assets[6], "location"),
			"target": "Reception",
		},
	]

	for mv in movements:
		asset_doc = frappe.get_doc("Asset", mv["asset"])
		existing = frappe.db.sql(
			"""
			SELECT am.name FROM `tabAsset Movement` am
			INNER JOIN `tabAsset Movement Item` ami ON ami.parent = am.name
			WHERE am.company = %s AND am.purpose = %s AND ami.asset = %s AND am.docstatus = 1
			LIMIT 1
			""",
			(company, mv["purpose"], mv["asset"]),
		)
		if existing:
			continue

		row = {"asset": mv["asset"], "company": company}
		if mv["purpose"] == "Transfer":
			row.update(
				{
					"source_location": mv["source"],
					"target_location": mv["target"],
				}
			)
		elif mv["purpose"] == "Issue":
			row.update(
				{
					"from_employee": mv.get("from_employee") or asset_doc.custodian,
					"to_employee": mv["to_employee"],
				}
			)

		movement = frappe.get_doc(
			{
				"doctype": "Asset Movement",
				"company": company,
				"purpose": mv["purpose"],
				"transaction_date": nowdate(),
				"assets": [row],
			}
		)
		movement.insert(ignore_permissions=True)
		movement.submit()


def validate_setup():
	"""Run post-setup validation checks."""
	settings = get_settings()
	company = settings.company_name
	checks = {
		"company": frappe.db.exists("Company", company),
		"asset_categories": frappe.db.count("Asset Category", {"asset_category_name": ["in", ["IT Equipment", "Office Furniture", "Vehicles"]]}),
		"locations": frappe.db.count("Location"),
		"items": frappe.db.count("Item", {"is_fixed_asset": 1}),
		"employees": frappe.db.count("Employee", {"company": company}),
		"assets_submitted": frappe.db.count("Asset", {"company": company, "docstatus": 1}),
		"assets_with_tags": frappe.db.count("Asset", {"company": company, "asset_tag": ["!=", ""]}),
		"asset_movements": frappe.db.count("Asset Movement", {"company": company, "docstatus": 1}),
	}

	print("\n=== Asset Management Setup Validation ===")
	for key, value in checks.items():
		print(f"  {key}: {value}")

	return checks
