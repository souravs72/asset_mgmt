# Copyright (c) 2026, Sourav Singh and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import today

from asset_mgmt.settings import get_settings


@frappe.whitelist()
def lookup_asset(asset_tag, company=None):
	"""Return asset details for a scanned tag or legacy code."""
	company = company or get_settings().company_name
	asset_tag = (asset_tag or "").strip()
	if not asset_tag:
		frappe.throw(_("Scan or enter an asset tag."))

	asset = frappe.db.get_value(
		"Asset",
		{"company": company, "asset_tag": asset_tag},
		[
			"name",
			"asset_name",
			"asset_tag",
			"location",
			"custodian",
			"operational_status",
			"legacy_asset_code",
		],
		as_dict=True,
	)
	if not asset:
		asset = frappe.db.get_value(
			"Asset",
			{"company": company, "legacy_asset_code": asset_tag},
			[
				"name",
				"asset_name",
				"asset_tag",
				"location",
				"custodian",
				"operational_status",
				"legacy_asset_code",
			],
			as_dict=True,
		)

	if not asset:
		return {"found": False, "asset_tag": asset_tag}

	return {"found": True, "asset": asset}


@frappe.whitelist()
def get_open_verification(location, company=None):
	"""Return the draft verification document for a location, if any."""
	company = company or get_settings().company_name
	name = frappe.db.get_value(
		"Asset Verification",
		{
			"company": company,
			"location": location,
			"docstatus": 0,
		},
		"name",
		order_by="modified desc",
	)
	return {"verification": name}


@frappe.whitelist()
def record_scan(asset_tag, verification, scanned_location=None, scanned_custodian=None):
	"""Record a physical scan on a draft Asset Verification document."""
	company = get_settings().company_name
	lookup = lookup_asset(asset_tag, company)
	if not lookup.get("found"):
		return {
			"ok": False,
			"verification_result": "Not Found",
			"message": _("No asset found for tag {0}").format(asset_tag),
		}

	asset = lookup["asset"]
	doc = frappe.get_doc("Asset Verification", verification)
	if doc.docstatus != 0:
		frappe.throw(_("Open a Draft verification to record scans."))

	scanned_location = scanned_location or doc.location or asset.location
	if scanned_location and frappe.db.exists("Location", scanned_location):
		pass
	elif doc.location:
		scanned_location = doc.location
	scanned_custodian = scanned_custodian or asset.custodian

	row = next((item for item in doc.items if item.asset == asset.name), None)
	if row:
		row.scanned_location = scanned_location
		row.scanned_custodian = scanned_custodian
		row.operational_status = asset.operational_status
	else:
		doc.append(
			"items",
			{
				"asset": asset.name,
				"scanned_location": scanned_location,
				"scanned_custodian": scanned_custodian,
				"operational_status": asset.operational_status,
				"verification_result": "Extra",
			},
		)
		row = doc.items[-1]

	doc.save(ignore_permissions=True)
	frappe.db.commit()

	return {
		"ok": True,
		"asset": asset.name,
		"verification_result": row.verification_result,
		"expected_location": row.expected_location,
		"scanned_location": row.scanned_location,
		"message": _("Scan recorded for {0}").format(asset.asset_name),
	}


@frappe.whitelist()
def get_mismatch_summary(company=None, verification=None):
	"""Summarize verification results and list mismatches for review."""
	company = company or get_settings().company_name
	filters = ["av.company = %s", "av.docstatus < 2"]
	values: list = [company]

	if verification:
		filters.append("av.name = %s")
		values.append(verification)

	where = " AND ".join(filters)
	summary = frappe.db.sql(
		f"""
		SELECT avi.verification_result, COUNT(*) AS count
		FROM `tabAsset Verification Item` avi
		INNER JOIN `tabAsset Verification` av ON av.name = avi.parent
		WHERE {where}
		GROUP BY avi.verification_result
		ORDER BY count DESC
		""",
		values,
		as_dict=True,
	)

	mismatches = frappe.db.sql(
		f"""
		SELECT
			av.name AS verification,
			av.location,
			avi.asset,
			a.asset_tag,
			a.asset_name,
			avi.expected_location,
			avi.scanned_location,
			avi.expected_custodian,
			avi.scanned_custodian,
			avi.verification_result
		FROM `tabAsset Verification Item` avi
		INNER JOIN `tabAsset Verification` av ON av.name = avi.parent
		LEFT JOIN `tabAsset` a ON a.name = avi.asset
		WHERE {where}
		  AND avi.verification_result IN ('Location Mismatch', 'Custodian Mismatch', 'Not Found', 'Extra')
		ORDER BY av.location, avi.verification_result, a.asset_tag
		LIMIT 500
		""",
		values,
		as_dict=True,
	)

	return {"summary": summary, "mismatches": mismatches}


@frappe.whitelist()
def submit_reviewed_corrections(verification):
	"""Submit a reviewed verification and write scanned values back to assets."""
	doc = frappe.get_doc("Asset Verification", verification)
	if doc.docstatus != 0:
		frappe.throw(_("Only draft verifications can be submitted with corrections."))

	mismatch_count = sum(
		1
		for row in doc.items
		if row.verification_result in {"Location Mismatch", "Custodian Mismatch", "Not Found", "Extra"}
	)
	if mismatch_count:
		frappe.msgprint(
			_("This verification has {0} mismatch/extra/not-found item(s). Submitting will update assets where scanned values differ.").format(
				mismatch_count
			),
			indicator="orange",
		)

	doc.update_assets_on_submit = 1
	doc.verify_only = 0
	doc.save(ignore_permissions=True)
	doc.submit()
	frappe.db.commit()

	return {
		"verification": doc.name,
		"status": doc.status,
		"mismatch_count": mismatch_count,
		"message": _("Verification submitted and asset corrections applied."),
	}


@frappe.whitelist()
def prepare_physical_scan(company=None):
	"""Cancel baseline verifications and recreate draft records ready for scanning."""
	company = company or get_settings().company_name
	cancelled = recreated = 0

	for name in frappe.get_all(
		"Asset Verification",
		filters={"company": company, "docstatus": ["<", 2]},
		pluck="name",
	):
		doc = frappe.get_doc("Asset Verification", name)
		if doc.docstatus == 1:
			doc.cancel()
		else:
			doc.delete()
		cancelled += 1

	assets_by_location = frappe.db.sql(
		"""
		SELECT location, name, asset_tag, custodian, operational_status
		FROM `tabAsset`
		WHERE company = %s
		  AND docstatus = 1
		  AND IFNULL(location, '') != ''
		  AND IFNULL(legacy_asset_code, '') != ''
		ORDER BY location, asset_tag
		""",
		company,
		as_dict=True,
	)

	grouped: dict[str, list] = {}
	for asset in assets_by_location:
		grouped.setdefault(asset.location, []).append(asset)

	for location, assets in grouped.items():
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
						"operational_status": asset.operational_status,
					}
					for asset in assets
				],
			}
		)
		doc.insert(ignore_permissions=True)
		recreated += 1

	frappe.db.commit()
	return {
		"cancelled_baseline": cancelled,
		"draft_verifications_created": recreated,
		"open_for_scan": frappe.db.count("Asset Verification", {"company": company, "docstatus": 0}),
	}


@frappe.whitelist()
def complete_all_scans(company=None):
	"""Record scans for every asset on open draft verifications (at verification location)."""
	company = company or get_settings().company_name
	scanned_docs = scanned_items = 0

	for name in frappe.get_all(
		"Asset Verification",
		filters={"company": company, "docstatus": 0},
		pluck="name",
	):
		doc = frappe.get_doc("Asset Verification", name)
		changed = False
		for row in doc.items:
			if row.scanned_location:
				continue
			asset = frappe.db.get_value(
				"Asset",
				row.asset,
				["location", "custodian", "operational_status"],
				as_dict=True,
			)
			if not asset:
				continue
			row.scanned_location = doc.location or asset.location
			row.scanned_custodian = asset.custodian
			row.operational_status = asset.operational_status or row.operational_status
			scanned_items += 1
			changed = True

		if changed:
			doc.save(ignore_permissions=True)
			scanned_docs += 1
		if scanned_docs % 10 == 0:
			frappe.db.commit()

	frappe.db.commit()
	return {
		"verifications_updated": scanned_docs,
		"items_scanned": scanned_items,
		"remaining_unscanned": frappe.db.sql(
			"""
			SELECT COUNT(*)
			FROM `tabAsset Verification Item` avi
			INNER JOIN `tabAsset Verification` av ON av.name = avi.parent
			WHERE av.company = %s
			  AND av.docstatus = 0
			  AND IFNULL(avi.scanned_location, '') = ''
			""",
			company,
		)[0][0],
	}


@frappe.whitelist()
def review_all_verifications(company=None):
	"""Return verification review summary across all open or submitted records."""
	company = company or get_settings().company_name
	summary = frappe.db.sql(
		"""
		SELECT avi.verification_result, COUNT(*) AS count
		FROM `tabAsset Verification Item` avi
		INNER JOIN `tabAsset Verification` av ON av.name = avi.parent
		WHERE av.company = %s AND av.docstatus < 2
		GROUP BY avi.verification_result
		ORDER BY count DESC
		""",
		company,
		as_dict=True,
	)

	by_location = frappe.db.sql(
		"""
		SELECT
			av.name,
			av.location,
			av.docstatus,
			SUM(CASE WHEN avi.verification_result = 'Match' THEN 1 ELSE 0 END) AS matched,
			SUM(CASE WHEN avi.verification_result IN (
				'Location Mismatch', 'Custodian Mismatch', 'Not Found', 'Extra'
			) THEN 1 ELSE 0 END) AS mismatched,
			SUM(CASE WHEN IFNULL(avi.scanned_location, '') = '' THEN 1 ELSE 0 END) AS pending
		FROM `tabAsset Verification` av
		INNER JOIN `tabAsset Verification Item` avi ON avi.parent = av.name
		WHERE av.company = %s AND av.docstatus < 2
		GROUP BY av.name, av.location, av.docstatus
		ORDER BY mismatched DESC, av.location
		""",
		company,
		as_dict=True,
	)

	mismatches = get_mismatch_summary(company=company)["mismatches"]
	return {
		"summary": summary,
		"by_location": by_location,
		"mismatch_count": len(mismatches),
		"mismatches_sample": mismatches[:20],
	}


@frappe.whitelist()
def submit_all_verifications(company=None, apply_corrections=0):
	"""Submit all draft verifications. Apply asset updates only when mismatches exist."""
	company = company or get_settings().company_name
	submitted = corrected = verify_only = failed = 0
	errors: list[str] = []

	for name in frappe.get_all(
		"Asset Verification",
		filters={"company": company, "docstatus": 0},
		pluck="name",
	):
		doc = frappe.get_doc("Asset Verification", name)
		unscanned = sum(1 for row in doc.items if not row.scanned_location)
		if unscanned:
			errors.append(f"{name}: {unscanned} item(s) still unscanned")
			failed += 1
			continue

		mismatch_count = sum(
			1
			for row in doc.items
			if row.verification_result
			in {"Location Mismatch", "Custodian Mismatch", "Not Found", "Extra"}
		)

		try:
			if mismatch_count and apply_corrections:
				doc.update_assets_on_submit = 1
				doc.verify_only = 0
				corrected += 1
			else:
				doc.update_assets_on_submit = 0
				doc.verify_only = 1
				verify_only += 1

			doc.save(ignore_permissions=True)
			doc.submit()
			submitted += 1
			if submitted % 10 == 0:
				frappe.db.commit()
		except Exception as exc:
			frappe.db.rollback()
			failed += 1
			if len(errors) < 10:
				errors.append(f"{name}: {exc}")

	frappe.db.commit()
	return {
		"submitted": submitted,
		"verify_only": verify_only,
		"with_corrections": corrected,
		"failed": failed,
		"errors": errors,
		"remaining_draft": frappe.db.count("Asset Verification", {"company": company, "docstatus": 0}),
	}


@frappe.whitelist()
def run_verification_workflow(company=None, apply_corrections=0):
	"""Scan all locations, review mismatches, and submit verifications."""
	scan = complete_all_scans(company=company)
	review = review_all_verifications(company=company)
	submit = submit_all_verifications(company=company, apply_corrections=apply_corrections)
	return {"scan": scan, "review": review, "submit": submit}
