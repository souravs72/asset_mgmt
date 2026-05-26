# Copyright (c) 2026, Sourav Singh and contributors
# For license information, please see license.txt

from frappe.modules.utils import sync_customizations

from asset_mgmt.config import ensure_default_settings
from asset_mgmt.setup import setup_asset_management


def sync_custom_fields():
	sync_customizations(app="asset_mgmt")


def after_install():
	sync_custom_fields()
	ensure_default_settings()
	setup_asset_management()
