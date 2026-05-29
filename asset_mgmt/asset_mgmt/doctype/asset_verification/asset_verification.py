# Copyright (c) 2026, Sourav Singh and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class AssetVerification(Document):
	def validate(self):
		self._validate_items()
		self._compute_verification_results()
		self.status = "Submitted" if self.docstatus == 1 else "Draft"
		self.verify_only = 0 if self.update_assets_on_submit else 1

	def on_submit(self):
		if self.update_assets_on_submit:
			self._update_assets_from_scan()
		self.status = "Submitted"
		self.verify_only = 0 if self.update_assets_on_submit else 1

	def on_cancel(self):
		if self.update_assets_on_submit:
			frappe.throw(
				_(
					"Cannot cancel this verification because Asset records were updated on submit. "
					"Create a new verification to correct asset data."
				)
			)
		self.status = "Draft"

	def _validate_items(self):
		if not self.items:
			frappe.throw(_("Add at least one asset verification item."))

	def _compute_verification_results(self):
		for row in self.items:
			if row.verification_result == "Extra":
				continue

			if not row.asset:
				row.verification_result = "Not Found"
				continue

			location_match = (row.scanned_location or "") == (row.expected_location or "")
			custodian_match = (row.scanned_custodian or "") == (row.expected_custodian or "")

			if not location_match:
				row.verification_result = "Location Mismatch"
			elif not custodian_match:
				row.verification_result = "Custodian Mismatch"
			else:
				row.verification_result = "Match"

	def _update_assets_from_scan(self):
		for row in self.items:
			if not row.asset:
				continue

			updates = {}
			asset = frappe.db.get_value("Asset", row.asset, ["location", "custodian"], as_dict=True)
			if not asset:
				continue

			if row.scanned_location and row.scanned_location != (asset.location or ""):
				updates["location"] = row.scanned_location

			if row.scanned_custodian and row.scanned_custodian != (asset.custodian or ""):
				updates["custodian"] = row.scanned_custodian

			if updates:
				frappe.db.set_value("Asset", row.asset, updates, update_modified=True)
