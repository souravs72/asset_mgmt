# Copyright (c) 2026, Sourav Singh and contributors
# For license information, please see license.txt

from frappe.model.document import Document

from asset_mgmt.settings import validate_demo_settings


class AssetMgmtSettings(Document):
	def validate(self):
		if self.enable_demo_setup:
			validate_demo_settings(self)
