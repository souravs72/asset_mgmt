# Copyright (c) 2026, Sourav Singh and contributors
# For license information, please see license.txt

import frappe

from asset_mgmt.settings import get_settings
from asset_mgmt.setup import setup_asset_management


def execute():
	settings = get_settings()
	if frappe.db.exists("Company", settings.company_name):
		return

	setup_asset_management()
