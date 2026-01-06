# Zen Browser Declarative Configuration

Declarative configuration management for Zen Browser. Define your complete browser setup—extensions, preferences, containers, and workspaces—in a single YAML file.

## Features

- **Declarative Configuration**: Define everything in `config.yaml`
- **Extension Management**: Automatically install extensions via Firefox policies
- **Toolbar Customization**: Declaratively configure toolbar layout and button positions
- **Semi-Automated Mods**: Script opens mod installation pages - you just click "Install"
- **Certificate Import**: Automatically import CA certificates for corporate networks
- **Container Configuration**: Set up Multi-Account Containers with domain assignments
- **Custom Search Engines**: Set your default search engine
- **Interactive Setup Guide**: HTML checklist for manual configuration steps
- **Reproducible**: Go from empty `.zen` folder to fully configured browser
- **Version Control Friendly**: Track your browser configuration in git

## Quick Start

### Installation

1. Clone this repository:
```bash
git clone <repo-url>
cd browser-conf
```

2. Install dependencies with uv:
```bash
uv sync
```

### Usage

1. Copy the example configuration:
```bash
cp examples/config.yaml config.yaml
```

2. Edit `config.yaml` to your preferences:
```bash
$EDITOR config.yaml
```

3. Apply the configuration:
```bash
uv run zen-apply config.yaml
```

The script will:
- Detect your Zen Browser installation and profile
- Generate `user.js` with your preferences
- Install `policies.json` for extensions and default search engine (requires sudo)
- Import CA certificates from `./certificates` directory (if present)
- Open Zen mod pages in your browser for easy installation
- Generate a setup guide (`~/.zen/setup-guide.html`) for manual steps
- Set up containers

4. Complete the manual steps in the setup guide that opens in your browser:
   - Click "Install" on each Zen mod page
   - Set up workspaces (optional)
   - Pin essential tabs (optional)

## Configuration

See `examples/config.yaml` for a fully documented configuration example.

### Main Sections

- **profile**: Profile name and Zen installation path
- **extensions**: Extensions to auto-install via Firefox policies
- **config**: Unified browser configuration using nested YAML (Firefox and Zen preferences)
- **toolbar**: Declarative toolbar layout and button positions
- **zen_mods**: CSS-based themes/customizations from the Zen theme store
- **certificates_dir**: Directory containing CA certificates to import (.crt or .pem files)
- **default_search_engine**: Set your default search engine
- **containers**: Container definitions (name, color, icon)
- **extension_settings**: Configure extension settings (e.g., Bitwarden server URL)

### Configuration Format

The `config` section uses nested YAML for all browser preferences (both Firefox and Zen). The nesting is automatically flattened to dot-notation when generating user.js:

```yaml
config:
  # Firefox preferences
  privacy:
    trackingprotection:
      enabled: true
  browser:
    startup:
      homepage: "about:blank"

  # Zen preferences (use zen.* prefix)
  zen:
    view:
      compact: true
    tabs:
      vertical: true
```

This generates:
```javascript
user_pref("privacy.trackingprotection.enabled", true);
user_pref("browser.startup.homepage", "about:blank");
user_pref("zen.view.compact", true);
user_pref("zen.tabs.vertical", true);
```

**Note**: For backward compatibility, the old format (`preferences` and `zen` as separate sections) is still supported.

## Requirements

- Zen Browser (native installation)
- Python 3.11+
- uv (Python package manager)
- sudo access (for installing policies.json)

## How It Works

### user.js
User preferences are written to `~/.zen/<profile>/user.js`. This file is automatically loaded by Firefox/Zen on startup and overrides default preferences.

### policies.json
Extensions are installed via `policies.json` placed in the Zen installation directory (`/usr/lib/zen-browser/distribution/policies.json`). This requires elevated permissions.

### Containers
Containers are configured via Firefox policies. They are created automatically by Firefox/Zen when the browser starts.

### Zen Mods
Zen mods are CSS-based themes and customizations from the [Zen theme store](https://zen-browser.app/mods/). The script provides **semi-automated installation**:

1. Fetches the theme store catalog from GitHub
2. Finds your configured mods by UUID or name
3. Opens each mod's installation page directly in Zen Browser (`zen-browser <url>`)
4. You simply click "Install" on each page

This approach works around Zen Browser's limitation that mods must be installed through the UI - the script does all the work of finding the right pages and opening them for you.

You can specify mods by their UUID or name. Browse available mods at [zen-browser.app/mods](https://zen-browser.app/mods/).

### Toolbar Customization
Declaratively configure your toolbar layout, button positions, and widget placements:

**How to set up:**

**Option 1: Using the helper script (easiest)**
1. Customize your toolbar manually in Zen Browser (right-click toolbar → "Customize Toolbar...")
2. Go to `about:config`
3. Search for `browser.uiCustomization.state`
4. Copy the JSON value
5. Run the helper script:
   ```bash
   uv run json-to-yaml
   ```
6. Paste the JSON and press Ctrl+D
7. Copy the YAML output into your config.yaml

**Option 2: Manual conversion**
1. Customize your toolbar in Zen Browser
2. Go to `about:config` and copy `browser.uiCustomization.state`
3. Convert the JSON to YAML format manually
4. Paste it into your config under the `toolbar` section

**Example**:
```yaml
toolbar:
  placements:
    widget-overflow-fixed-list: []
    unified-extensions-area:
      - ublock0_raymondhill_net-browser-action
    nav-bar:
      - back-button
      - forward-button
      - stop-reload-button
      - urlbar-container
      - downloads-button
    TabsToolbar:
      - tabbrowser-tabs
  seen:
    - developer-button
    - ublock0_raymondhill_net-browser-action
  currentVersion: 20
```

The toolbar state is written to `user.js` as `browser.uiCustomization.state` and applied on browser startup.

### Setup Guide
For configuration that cannot be automated (workspaces, essential tabs), the script generates an HTML setup guide at `~/.zen/setup-guide.html`. This guide:
- Lists all manual configuration steps with clear instructions
- Includes checkboxes to track your progress
- Links directly to all mod installation pages
- Opens automatically in your browser when the script completes

### Certificates
Import custom CA certificates (e.g., for corporate networks, self-signed certificates) using the `certificates_dir` option:

1. Place your certificate files (`.crt` or `.pem`) in the configured directory (default: `./certificates`)
2. The script includes them in `policies.json` using Firefox's `Certificates.Install` policy
3. Firefox/Zen automatically imports them on startup - no external tools needed!

**Example**:
```yaml
certificates_dir: certificates  # Relative to config file
```

Then add your certificates:
```
certificates/
  ├── company-root-ca.crt
  └── internal-ca.pem
```

The generated `policies.json` will include:
```json
"Certificates": {
  "Install": [
    "/absolute/path/to/certificates/company-root-ca.crt",
    "/absolute/path/to/certificates/internal-ca.pem"
  ]
}
```


## Troubleshooting

### Extensions not installing
Make sure `policies.json` is installed correctly:
```bash
cat /usr/lib/zen-browser/distribution/policies.json
```

If the file doesn't exist, the script may not have had sufficient permissions.

### Preferences not applying
Check that `user.js` was created:
```bash
cat ~/.zen/<profile-name>.default/user.js
```

Restart Zen Browser after applying configuration.

## License

MIT
