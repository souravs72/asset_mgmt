frappe.pages["asset-tag-scan"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Asset Tag Scan"),
		single_column: true,
	});

	const company = frappe.defaults.get_default("company");
	let current_verification = null;

	const fields = [
		{
			fieldname: "location",
			label: __("Location"),
			fieldtype: "Link",
			options: "Location",
			reqd: 1,
			change() {
				load_verification();
			},
		},
		{
			fieldname: "verification",
			label: __("Verification"),
			fieldtype: "Link",
			options: "Asset Verification",
			read_only: 1,
		},
		{
			fieldname: "scanned_location",
			label: __("Scanned Location"),
			fieldtype: "Link",
			options: "Location",
		},
		{
			fieldname: "scanned_custodian",
			label: __("Scanned Custodian"),
			fieldtype: "Link",
			options: "Employee",
		},
		{
			fieldname: "asset_tag",
			label: __("Asset Tag"),
			fieldtype: "Data",
			reqd: 1,
		},
	];

	const form = new frappe.ui.FieldGroup({
		fields,
		body: page.main,
	});

	form.make();
	form.fields_dict.location.set_value("");
	form.fields_dict.asset_tag.$input.on("keydown", (event) => {
		if (event.key === "Enter") {
			event.preventDefault();
			record_scan();
		}
	});

	const result = $('<div class="scan-result margin-top"></div>').appendTo(page.main);
	const history = $('<div class="scan-history margin-top"></div>').appendTo(page.main);

	page.set_primary_action(__("Scan"), () => record_scan());

	page.add_inner_button(__("Open Verification"), () => {
		if (current_verification) {
			frappe.set_route("Form", "Asset Verification", current_verification);
		}
	});

	page.add_inner_button(__("Mismatch Summary"), () => {
		frappe.set_route("query-report", "Asset Verification Summary");
	});

	function load_verification() {
		const location = form.get_value("location");
		if (!location) {
			current_verification = null;
			form.set_value("verification", "");
			form.set_value("scanned_location", "");
			return;
		}

		frappe.call({
			method: "asset_mgmt.api.verification.get_open_verification",
			args: { location, company },
			callback(response) {
				current_verification = response.message.verification;
				form.set_value("verification", current_verification || "");
				form.set_value("scanned_location", location);
			},
		});
	}

	function record_scan() {
		const asset_tag = (form.get_value("asset_tag") || "").trim();
		const location = form.get_value("location");
		const verification = form.get_value("verification");

		if (!location) {
			frappe.msgprint(__("Select a location first."));
			return;
		}
		if (!verification) {
			frappe.msgprint(__("No draft verification found for this location."));
			return;
		}
		if (!asset_tag) {
			frappe.msgprint(__("Scan or enter an asset tag."));
			return;
		}

		frappe.call({
			method: "asset_mgmt.api.verification.record_scan",
			args: {
				asset_tag,
				verification,
				scanned_location: form.get_value("scanned_location") || location,
				scanned_custodian: form.get_value("scanned_custodian"),
			},
			freeze: true,
			callback(response) {
				const message = response.message || {};
				const indicator = message.ok ? "green" : "red";
				const result_label = message.verification_result || __("Not Found");

				result.html(
					`<div class="alert alert-${message.ok ? "success" : "danger"}">
						<strong>${frappe.utils.escape_html(message.message || "")}</strong><br>
						${__("Result")}: ${frappe.utils.escape_html(result_label)}
					</div>`
				);

				history.prepend(
					`<div class="text-muted small">${frappe.utils.escape_html(asset_tag)} → ${frappe.utils.escape_html(result_label)}</div>`
				);
				form.set_value("asset_tag", "");
				form.fields_dict.asset_tag.$input.focus();
			},
		});
	}

	frappe.breadcrumbs.add("Asset Mgmt");
};
