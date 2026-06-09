frappe.ui.form.on("Asset Verification", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}

		frm.add_custom_button(__("Open Tag Scan"), () => {
			frappe.set_route("page", "asset-tag-scan");
		});

		frm.add_custom_button(__("Mismatch Summary"), () => {
			frappe.set_route("query-report", "Asset Verification Summary", {
				verification: frm.doc.name,
			});
		});

		if (frm.doc.docstatus === 0 && frm.doc.items?.length) {
			frm.add_custom_button(
				__("Submit with Corrections"),
				() => {
					frappe.confirm(
						__(
							"This will submit the verification and update Asset location/custodian from scanned values. Continue?"
						),
						() => {
							frappe.call({
								method: "asset_mgmt.api.verification.submit_reviewed_corrections",
								args: { verification: frm.doc.name },
								freeze: true,
								callback() {
									frm.reload_doc();
								},
							});
						}
					);
				},
				__("Actions")
			);
		}
	},
});
