# Copyright (c) 2026, Sourav Singh and contributors
# For license information, please see license.txt

import json
import os

import frappe
from frappe.modules.utils import sync_customizations_for_doctype

from asset_mgmt.settings import ensure_default_settings
from asset_mgmt.setup import setup_asset_management


def sync_custom_fields():
	"""Sync Asset custom fields from the app custom folder."""
	folder = os.path.join(frappe.get_app_path("asset_mgmt"), "custom")
	if not os.path.exists(folder):
		return

	for fname in os.listdir(folder):
		if not fname.endswith(".json"):
			continue
		with open(os.path.join(folder, fname)) as handle:
			data = json.load(handle)
		sync_customizations_for_doctype(data, folder, fname)

	frappe.clear_cache()


def after_install():
	sync_custom_fields()
	ensure_default_settings()
	setup_asset_management()
