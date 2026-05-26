# Asset Mgmt

ERPNext fixed asset management customizations for AscraTech clients.

## Features

- Custom Asset fields: `asset_tag`, `asset_tag_type`, `operational_status`
- Configurable setup via **Asset Mgmt Settings** (Single DocType)
- Optional demo company, masters, and 18 sample assets on install
- Automatic site finalization (skips setup wizard redirect loop)
- CSV import templates under `asset_mgmt/fixtures/import_templates/`

## Configuration

After install, open **Asset Mgmt Settings** to change:

- Company name / abbreviation / country / currency
- Enable or disable demo setup and demo data
- Finalize site on install (setup wizard bypass)

Re-run demo setup manually:

```bash
bench --site <site> execute asset_mgmt.setup.setup_asset_management
```

Validate:

```bash
bench --site <site> execute asset_mgmt.setup.validate_setup
```

## Install

```bash
bench get-app https://github.com/Ascra-Tech/asset_mgmt.git
bench --site <site> install-app erpnext
bench --site <site> install-app hrms
bench --site <site> install-app asset_mgmt
```

## License

MIT
