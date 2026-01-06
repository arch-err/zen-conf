"""Main application logic for applying Zen Browser configuration."""

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.request import urlopen

import yaml
from jinja2 import Environment, FileSystemLoader


class ZenConfig:
    """Manages Zen Browser configuration application."""

    def __init__(self, config_path: Path):
        """Initialize with configuration file path."""
        self.config_path = config_path
        self.config = self._load_config()
        self.home = Path.home()

    def _load_config(self) -> dict[str, Any]:
        """Load YAML configuration file."""
        with open(self.config_path) as f:
            return yaml.safe_load(f)

    @staticmethod
    def _flatten_dict(d: dict, parent_key: str = "", sep: str = ".") -> dict:
        """
        Flatten nested dictionary into dot-notation keys.

        Special handling: If a dict contains "enabled" AND other keys, the "enabled"
        value is assigned to the parent key itself, while other keys nest normally.
        If "enabled" is the ONLY key, it nests normally.

        Examples:
            {"view": {"compact": true}}
            -> {"view.compact": true}

            {"glance": {"enabled": false}}
            -> {"glance.enabled": false}  (only "enabled", so nests normally)

            {"view": {"compact": {"enabled": true, "enable-at-startup": true}}}
            -> {"view.compact": true, "view.compact.enable-at-startup": true}
               (has other keys, so "enabled" becomes parent value)
        """
        items = []
        for k, v in d.items():
            new_key = f"{parent_key}{sep}{k}" if parent_key else k
            if isinstance(v, dict):
                # Special case: if dict has "enabled" key AND other keys
                if "enabled" in v and len(v) > 1:
                    # Assign the "enabled" value to the parent key
                    items.append((new_key, v["enabled"]))
                    # Continue flattening other keys
                    remaining = {key: val for key, val in v.items() if key != "enabled"}
                    items.extend(ZenConfig._flatten_dict(remaining, new_key, sep=sep).items())
                else:
                    # Normal recursive flattening (including when only "enabled" exists)
                    items.extend(ZenConfig._flatten_dict(v, new_key, sep=sep).items())
            else:
                items.append((new_key, v))
        return dict(items)

    def detect_zen_paths(self) -> tuple[Path, Path]:
        """
        Detect Zen Browser installation and profile paths.

        Returns:
            Tuple of (installation_path, profile_path)
        """
        # Profile path (native installation)
        zen_dir = self.home / ".zen"

        if not zen_dir.exists():
            print(f"Creating Zen profile directory: {zen_dir}")
            zen_dir.mkdir(parents=True, exist_ok=True)

        # Find or create profile
        profile_name = self.config.get("profile", {}).get("name", "default")

        # Check for profiles.ini
        profiles_ini = zen_dir / "profiles.ini"
        profile_path = None
        profile_exists_in_ini = False

        if profiles_ini.exists():
            # Parse profiles.ini to find the profile
            profile_path = self._parse_profiles_ini(profiles_ini, profile_name)
            if profile_path:
                profile_exists_in_ini = True

        if not profile_path:
            # Create new profile directory
            profile_path = zen_dir / f"{profile_name}.default"
            print(f"Creating new profile: {profile_path}")
            profile_path.mkdir(parents=True, exist_ok=True)
        else:
            # Profile exists in ini, but ensure directory exists
            if not profile_path.exists():
                print(f"Creating profile directory: {profile_path}")
                profile_path.mkdir(parents=True, exist_ok=True)

        # Detect Zen installation path
        zen_install_path = self._detect_zen_installation()

        # Ensure profile is registered in profiles.ini
        # Always update to ensure Install sections point to our profile
        self._register_profile_in_ini(zen_dir, profile_name, profile_path, zen_install_path, update_only=profile_exists_in_ini)

        # Ensure profile is set in installs.ini
        self._update_installs_ini(zen_dir, profile_path, zen_install_path)

        return zen_install_path, profile_path

    def _parse_profiles_ini(self, profiles_ini: Path, profile_name: str) -> Path | None:
        """Parse profiles.ini to find profile path."""
        # Simple parser for profiles.ini
        content = profiles_ini.read_text()
        lines = content.split('\n')

        current_section = None
        section_data = {}

        for line in lines:
            line = line.strip()
            if line.startswith('[') and line.endswith(']'):
                current_section = line[1:-1]
                section_data[current_section] = {}
            elif '=' in line and current_section:
                key, value = line.split('=', 1)
                section_data[current_section][key.strip()] = value.strip()

        # Find profile
        for section, data in section_data.items():
            if section.startswith('Profile'):
                if data.get('Name') == profile_name:
                    if data.get('IsRelative') == '1':
                        return self.home / ".zen" / data.get('Path', '')
                    else:
                        return Path(data.get('Path', ''))

        return None

    def _register_profile_in_ini(self, zen_dir: Path, profile_name: str, profile_path: Path, zen_install_path: Path, update_only: bool = False) -> None:
        """Register profile in profiles.ini or update Install sections."""
        profiles_ini = zen_dir / "profiles.ini"

        # Parse existing profiles.ini if it exists
        existing_profiles = []
        install_sections = {}  # Dict of install_hash -> {key: value}

        if profiles_ini.exists():
            content = profiles_ini.read_text()
            lines = content.split('\n')

            current_section = None
            for line in lines:
                line = line.strip()
                if line.startswith('[') and line.endswith(']'):
                    current_section = line[1:-1]
                    if current_section.startswith('Profile'):
                        existing_profiles.append({})
                    elif current_section.startswith('Install'):
                        # Extract install hash
                        install_hash = current_section.replace('Install', '')
                        install_sections[install_hash] = {}
                elif '=' in line and current_section:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip()

                    if current_section.startswith('Profile') and existing_profiles:
                        existing_profiles[-1][key] = value
                    elif current_section.startswith('Install'):
                        install_hash = current_section.replace('Install', '')
                        install_sections[install_hash][key] = value

        # Determine relative path
        relative_path = profile_path.relative_to(zen_dir)

        # Build new profiles.ini content
        lines = ['[General]']
        lines.append('StartWithLastProfile=1')
        lines.append('Version=2')
        lines.append('')

        if update_only:
            # Just update Install sections, keep existing profiles as-is
            # But make sure our profile has Default=1
            current_prof_num = 0
            for i, prof in enumerate(existing_profiles):
                lines.append(f'[Profile{current_prof_num}]')
                for key, value in prof.items():
                    # Remove Default from all profiles first
                    if key == 'Default':
                        continue
                    lines.append(f'{key}={value}')
                # Add Default=1 to our profile
                if prof.get('Name') == profile_name:
                    lines.append('Default=1')
                lines.append('')
                current_prof_num += 1
        else:
            # Add existing profiles (remove Default=1 from all)
            current_prof_num = 0
            for i, prof in enumerate(existing_profiles):
                if prof.get('Name') == profile_name:
                    continue  # Skip if we're re-adding it
                lines.append(f'[Profile{current_prof_num}]')
                for key, value in prof.items():
                    if key == 'Default':
                        continue  # Remove Default flag from other profiles
                    lines.append(f'{key}={value}')
                lines.append('')
                current_prof_num += 1

            # Add new/updated profile as default
            lines.append(f'[Profile{current_prof_num}]')
            lines.append(f'Name={profile_name}')
            lines.append('IsRelative=1')
            lines.append(f'Path={relative_path}')
            lines.append('Default=1')
            lines.append('')

        # Add ALL Install sections, updating them to point to our profile
        # Only write Install sections if they already exist (Zen will create them on first run)
        for install_hash in sorted(install_sections.keys()):
            lines.append(f'[Install{install_hash}]')
            lines.append(f'Default={relative_path}')
            lines.append('Locked=1')
            lines.append('')

        # Write profiles.ini
        profiles_ini.write_text('\n'.join(lines))
        if update_only:
            print(f"Updated Install sections in profiles.ini to use profile: {profile_name}")
        else:
            print(f"Registered profile '{profile_name}' in profiles.ini")

    def _update_installs_ini(self, zen_dir: Path, profile_path: Path, zen_install_path: Path) -> None:
        """Update installs.ini to point to our profile."""
        installs_ini = zen_dir / "installs.ini"
        profiles_ini = zen_dir / "profiles.ini"

        # Determine relative path
        relative_path = profile_path.relative_to(zen_dir)

        # Get ALL install hashes from profiles.ini (there may be multiple for different installations)
        install_hashes_from_profiles = set()
        if profiles_ini.exists():
            content = profiles_ini.read_text()
            for line in content.split('\n'):
                line = line.strip()
                if line.startswith('[Install') and line.endswith(']'):
                    install_hash = line[8:-1]  # Extract hash from [InstallXXXX]
                    install_hashes_from_profiles.add(install_hash)

        # Parse existing installs.ini if it exists
        install_sections = {}
        if installs_ini.exists():
            content = installs_ini.read_text()
            lines = content.split('\n')

            current_section = None
            for line in lines:
                line = line.strip()
                if line.startswith('[') and line.endswith(']'):
                    current_section = line[1:-1]
                    install_sections[current_section] = {}
                elif '=' in line and current_section:
                    key, value = line.split('=', 1)
                    install_sections[current_section][key.strip()] = value.strip()

        # Merge: use hashes from profiles.ini (which are the authoritative ones)
        all_hashes = install_hashes_from_profiles if install_hashes_from_profiles else set(install_sections.keys())

        # Only write installs.ini if we have actual hashes
        # (Zen will create them on first run based on its own hash calculation)
        if all_hashes:
            # Update ALL installation sections to point to our profile
            lines = []
            for install_hash in sorted(all_hashes):
                lines.append(f'[{install_hash}]')
                lines.append(f'Default={relative_path}')
                lines.append('Locked=1')
                lines.append('')

            # Write installs.ini
            installs_ini.write_text('\n'.join(lines))
            print(f"Updated installs.ini ({len(all_hashes)} installation(s)) to use profile: {relative_path}")
        else:
            # Don't create installs.ini yet - let Zen create it with the correct hash
            if installs_ini.exists():
                installs_ini.unlink()
            print("No installation hashes found - Zen will create installs.ini on first run")

    def _detect_zen_installation(self) -> Path:
        """Detect Zen Browser installation directory."""
        zen_path_config = self.config.get("profile", {}).get("zen_path", "auto")

        if zen_path_config != "auto":
            path = Path(zen_path_config)
            if path.exists():
                return path
            else:
                print(f"Warning: Configured Zen path {path} does not exist")

        # Try to find via which command first (most reliable)
        try:
            result = subprocess.run(
                ["which", "zen-browser"],
                capture_output=True,
                text=True,
                check=False
            )
            if result.returncode == 0:
                zen_bin_path = Path(result.stdout.strip())

                # Check if it's a shell script that execs the real binary
                if zen_bin_path.exists() and zen_bin_path.stat().st_size < 10000:
                    try:
                        content = zen_bin_path.read_text()
                        # Look for exec /path/to/zen lines
                        for line in content.split('\n'):
                            if 'exec' in line and ('zen' in line.lower() or 'browser' in line.lower()):
                                # Extract path from exec command
                                parts = line.split()
                                for part in parts:
                                    if 'zen' in part.lower() and '/' in part:
                                        # Found the real binary path
                                        real_bin = Path(part.strip('"\''))
                                        if real_bin.exists():
                                            install_dir = real_bin.parent
                                            print(f"Found Zen Browser installation: {install_dir}")
                                            return install_dir
                    except Exception:
                        pass

                # Fallback: use parent directory
                zen_bin_path = zen_bin_path.resolve()
                install_dir = zen_bin_path.parent
                if (install_dir / "zen-bin").exists() or (install_dir / "zen").exists():
                    print(f"Found Zen Browser installation: {install_dir}")
                    return install_dir
                # Maybe it's in parent's parent (e.g., /usr/bin -> /usr)
                install_dir = zen_bin_path.parent.parent
                if install_dir.exists():
                    print(f"Found Zen Browser installation: {install_dir}")
                    return install_dir
        except Exception as e:
            print(f"Warning: Error detecting Zen installation via which: {e}")

        # Common installation paths for Zen Browser on Linux
        possible_paths = [
            Path("/opt/zen-browser"),
            Path("/opt/zen-browser-bin"),
            Path("/usr/lib/zen-browser"),
            Path("/usr/local/lib/zen-browser"),
            self.home / ".local/share/zen-browser",
        ]

        for path in possible_paths:
            if path.exists():
                print(f"Found Zen Browser installation: {path}")
                return path

        print("Warning: Could not auto-detect Zen Browser installation path")
        print("Policies may need to be installed manually")
        return Path("/opt/zen-browser")  # Default fallback

    def generate_user_js(self, profile_path: Path) -> None:
        """Generate user.js from configuration."""
        print("Generating user.js...")

        # Load template
        template_dir = Path(__file__).parent.parent / "templates"
        env = Environment(loader=FileSystemLoader(template_dir))
        template = env.get_template("user.js.j2")

        workspaces = self.config.get("workspaces", [])

        # New unified config format (nested)
        preferences = {}
        zen_preferences = {}

        if "config" in self.config:
            # Flatten the entire config section
            flattened_config = self._flatten_dict(self.config["config"])

            # Separate zen.* preferences from regular preferences
            for key, value in flattened_config.items():
                if key.startswith("zen."):
                    # Remove "zen." prefix for zen_preferences
                    zen_key = key[4:]  # Remove "zen." prefix
                    zen_preferences[zen_key] = value
                else:
                    preferences[key] = value
        else:
            # Backward compatibility: support old format
            preferences = self.config.get("preferences", {})

            # Support old flat format: zen_preferences
            if "zen_preferences" in self.config:
                zen_preferences.update(self.config["zen_preferences"])

            # Support old nested format: zen
            if "zen" in self.config:
                zen_nested = self.config["zen"]
                flattened = self._flatten_dict(zen_nested)
                zen_preferences.update(flattened)

        # Handle toolbar customization
        toolbar_state = None
        if "toolbar" in self.config:
            toolbar_config = self.config["toolbar"]
            # Convert toolbar config to JSON string for browser.uiCustomization.state
            toolbar_state = json.dumps(toolbar_config, separators=(',', ':'))
            print("  Including toolbar customization")

        user_js_content = template.render(
            preferences=preferences,
            zen_preferences=zen_preferences,
            workspaces=workspaces,
            toolbar_state=toolbar_state,
        )

        # Write to profile
        user_js_path = profile_path / "user.js"
        user_js_path.write_text(user_js_content)
        print(f"Created: {user_js_path}")

    def generate_policies_json(self, zen_install_path: Path) -> None:
        """Generate and install policies.json."""
        print("Generating policies.json...")

        # Load template
        template_dir = Path(__file__).parent.parent / "templates"
        env = Environment(loader=FileSystemLoader(template_dir))
        template = env.get_template("policies.json.j2")

        # Render template
        extensions = self.config.get("extensions", {})
        containers = self.config.get("containers", [])
        extension_settings = self.config.get("extension_settings", {})
        default_search_engine = self.config.get("default_search_engine")
        certificates = self.get_certificate_paths()

        if certificates:
            print(f"  Found {len(certificates)} certificate(s) to install")

        policies_content = template.render(
            extensions=extensions,
            containers=containers,
            extension_settings=extension_settings,
            default_search_engine=default_search_engine,
            certificates=certificates,
        )

        # Validate JSON
        try:
            json.loads(policies_content)
        except json.JSONDecodeError as e:
            print(f"Error: Generated invalid policies.json: {e}")
            sys.exit(1)

        # Determine installation path
        policies_dir = zen_install_path / "distribution"
        policies_path = policies_dir / "policies.json"

        print(f"Installing policies to: {policies_path}")

        # Create distribution directory if needed
        if not policies_dir.exists():
            try:
                # Try without sudo first
                policies_dir.mkdir(parents=True, exist_ok=True)
            except PermissionError:
                print("Need elevated permissions to create distribution directory...")
                # Use sudo
                try:
                    subprocess.run(
                        ["sudo", "mkdir", "-p", str(policies_dir)],
                        check=True
                    )
                except subprocess.CalledProcessError:
                    print("Error: Failed to create distribution directory")
                    sys.exit(1)

        # Write policies.json
        temp_policies = Path("/tmp/policies.json")
        temp_policies.write_text(policies_content)

        try:
            # Try to copy without sudo first
            shutil.copy(temp_policies, policies_path)
        except PermissionError:
            print("Need elevated permissions to install policies.json...")
            # Use sudo
            try:
                subprocess.run(
                    ["sudo", "cp", str(temp_policies), str(policies_path)],
                    check=True
                )
            except subprocess.CalledProcessError:
                print("Error: Failed to install policies.json")
                sys.exit(1)

        print(f"Installed: {policies_path}")
        temp_policies.unlink()

    def create_search_engine_bookmarks(self, profile_path: Path) -> None:
        """Create bookmark keywords for custom search engines."""
        search_engines = self.config.get("search_engines", [])

        if not search_engines:
            return

        print("Creating custom search engine bookmarks...")

        import sqlite3

        places_db = profile_path / "places.sqlite"

        # Check if database exists (profile might be new)
        if not places_db.exists():
            print("  Warning: places.sqlite doesn't exist yet. Custom search engines will be added on first browser launch.")
            # We'll need to create it after first launch
            return

        try:
            conn = sqlite3.connect(places_db)
            cursor = conn.cursor()

            for engine in search_engines:
                keyword = engine.get("keyword")
                name = engine.get("name", keyword)
                url = engine.get("url")

                if not keyword or not url:
                    continue

                # Check if bookmark with this keyword already exists
                cursor.execute(
                    "SELECT id FROM moz_keywords WHERE keyword = ?",
                    (keyword,)
                )
                existing = cursor.fetchone()

                if existing:
                    print(f"  Updating: {name} ({keyword})")
                    # Update existing bookmark
                    keyword_id = existing[0]
                    cursor.execute(
                        "SELECT place_id FROM moz_keywords WHERE id = ?",
                        (keyword_id,)
                    )
                    place_id = cursor.fetchone()[0]

                    # Update URL
                    cursor.execute(
                        "UPDATE moz_places SET url = ?, title = ? WHERE id = ?",
                        (url, name, place_id)
                    )
                else:
                    print(f"  Creating: {name} ({keyword})")

                    # Create new place (URL)
                    cursor.execute(
                        "INSERT INTO moz_places (url, title, rev_host, visit_count, hidden, typed, frecency, guid) "
                        "VALUES (?, ?, '', 0, 0, 0, -1, lower(hex(randomblob(8))) || '-' || "
                        "lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))),2) || '-' || "
                        "substr('89ab',abs(random()) % 4 + 1, 1) || substr(lower(hex(randomblob(2))),2) || '-' || "
                        "lower(hex(randomblob(6))))",
                        (url, name)
                    )
                    place_id = cursor.lastrowid

                    # Create bookmark in "Other Bookmarks" folder (id=4)
                    timestamp = int(datetime.now().timestamp() * 1000000)
                    cursor.execute(
                        "INSERT INTO moz_bookmarks (type, fk, parent, position, title, dateAdded, lastModified, guid) "
                        "VALUES (1, ?, 4, "
                        "(SELECT IFNULL(MAX(position), 0) + 1 FROM moz_bookmarks WHERE parent = 4), "
                        "?, ?, ?, lower(hex(randomblob(8))) || '-' || "
                        "lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))),2) || '-' || "
                        "substr('89ab',abs(random()) % 4 + 1, 1) || substr(lower(hex(randomblob(2))),2) || '-' || "
                        "lower(hex(randomblob(6))))",
                        (place_id, name, timestamp, timestamp)
                    )
                    bookmark_id = cursor.lastrowid

                    # Create keyword
                    cursor.execute(
                        "INSERT INTO moz_keywords (keyword, place_id, post_data) VALUES (?, ?, NULL)",
                        (keyword, place_id)
                    )

            conn.commit()
            conn.close()
            print(f"  Created {len(search_engines)} custom search engine(s)")

        except sqlite3.Error as e:
            print(f"  Warning: Could not create search engines: {e}")
            print("  You may need to create them manually or run the script after first browser launch.")

    def fetch_theme_store(self) -> dict[str, Any]:
        """Fetch the Zen theme store themes.json."""
        theme_store_url = "https://raw.githubusercontent.com/zen-browser/theme-store/main/themes.json"
        try:
            with urlopen(theme_store_url) as response:
                return json.loads(response.read())
        except Exception as e:
            print(f"Warning: Could not fetch theme store: {e}")
            return {}

    def find_mod_in_store(self, mod_config: dict, theme_store: dict) -> tuple[str, dict] | None:
        """Find a mod in the theme store by ID or name.

        Returns tuple of (theme_id, theme_data) or None if not found.
        """
        mod_id = mod_config.get("id")
        mod_name = mod_config.get("name")

        # theme_store is a dict where keys are theme IDs and values are theme data
        # Check by ID first
        if mod_id and mod_id in theme_store:
            return (mod_id, theme_store[mod_id])

        # Search by name
        if mod_name:
            for theme_id, theme_data in theme_store.items():
                if theme_data.get("name") == mod_name:
                    return (theme_id, theme_data)

        return None

    def download_file(self, url: str, dest_path: Path) -> bool:
        """Download a file from URL to destination path."""
        try:
            with urlopen(url) as response:
                dest_path.write_bytes(response.read())
            return True
        except Exception as e:
            print(f"  Warning: Could not download {url}: {e}")
            return False

    def install_zen_mods(self, profile_path: Path) -> list[dict]:
        """Open Zen mod installation pages in browser.

        Returns a list of mods that need manual installation.
        """
        zen_mods = self.config.get("zen_mods", [])

        print("Opening Zen mods for installation...")

        if not zen_mods:
            print("  No mods configured")
            return []

        # Fetch theme store to look up mod IDs
        theme_store = self.fetch_theme_store()
        if not theme_store:
            print("  Warning: Could not fetch theme store, skipping mod configuration")
            return []

        # Resolve mod names/IDs from config and open installation pages
        opened_mods = []
        for mod_config in zen_mods:
            result = self.find_mod_in_store(mod_config, theme_store)
            if not result:
                mod_name = mod_config.get("name", "Unknown")
                print(f"  Warning: Mod '{mod_name}' not found in theme store, skipping")
                continue

            theme_id, theme_data = result
            theme_name = theme_data.get("name", mod_config.get("name", "Unknown"))

            # Open mod installation page in Zen Browser
            mod_url = f"https://zen-browser.app/mods/{theme_id}/"
            try:
                subprocess.Popen(
                    ['zen-browser', mod_url],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                print(f"  Opened: {theme_name}")
                opened_mods.append({
                    "name": theme_name,
                    "id": theme_id,
                    "url": mod_url
                })
            except Exception as e:
                print(f"  Warning: Could not open browser for '{theme_name}': {e}")

        if opened_mods:
            print(f"\n  Opened {len(opened_mods)} mod(s) in browser - click 'Install' on each page")

        return opened_mods

    def get_certificate_paths(self) -> list[str]:
        """Get list of certificate file paths for Firefox policies.

        Returns absolute paths to all .crt and .pem files in the certificates directory.
        """
        # Get certificates directory from config (default: ./certificates)
        certs_dir_config = self.config.get("certificates_dir", "certificates")
        certs_dir = Path(certs_dir_config)

        # Make path absolute relative to config file if it's relative
        if not certs_dir.is_absolute():
            certs_dir = self.config_path.parent / certs_dir

        if not certs_dir.exists():
            return []

        # Find all certificate files and return as absolute paths
        cert_files = list(certs_dir.glob("*.crt")) + list(certs_dir.glob("*.pem"))
        return [str(cert_file.resolve()) for cert_file in cert_files]

    def generate_setup_guide(self, zen_dir: Path, workspaces: list[dict]) -> Path:
        """Generate an HTML setup guide for manual configuration steps.

        Args:
            zen_dir: Path to Zen profile directory
            workspaces: List of workspace configurations from config

        Returns:
            Path to generated setup guide
        """
        guide_path = zen_dir / "setup-guide.html"

        # Build HTML content
        html_parts = [
            '<!DOCTYPE html>',
            '<html>',
            '<head>',
            '    <meta charset="UTF-8">',
            '    <title>Zen Browser Setup Guide</title>',
            '    <style>',
            '        body {',
            '            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;',
            '            max-width: 800px;',
            '            margin: 40px auto;',
            '            padding: 20px;',
            '            background: #1a1a1a;',
            '            color: #e0e0e0;',
            '        }',
            '        h1 { color: #6b9eff; }',
            '        h2 { color: #8ab4ff; margin-top: 30px; }',
            '        h3 { color: #a0c4ff; margin-top: 20px; }',
            '        h4 { color: #b8d4ff; margin-top: 15px; margin-bottom: 10px; }',
            '        .step {',
            '            background: #2a2a2a;',
            '            border-left: 4px solid #6b9eff;',
            '            padding: 15px;',
            '            margin: 15px 0;',
            '            border-radius: 4px;',
            '        }',
            '        .checkbox-item {',
            '            margin: 10px 0;',
            '            padding: 8px;',
            '            background: #333;',
            '            border-radius: 4px;',
            '        }',
            '        input[type="checkbox"] {',
            '            margin-right: 10px;',
            '            transform: scale(1.2);',
            '        }',
            '        a { color: #6b9eff; text-decoration: none; }',
            '        a:hover { text-decoration: underline; }',
            '        code {',
            '            background: #333;',
            '            padding: 2px 6px;',
            '            border-radius: 3px;',
            '            font-family: "Courier New", monospace;',
            '        }',
            '        table {',
            '            width: 100%;',
            '            border-collapse: collapse;',
            '            margin: 15px 0;',
            '            background: #2a2a2a;',
            '            border-radius: 4px;',
            '            overflow: hidden;',
            '        }',
            '        th, td {',
            '            padding: 12px;',
            '            text-align: left;',
            '            border-bottom: 1px solid #3a3a3a;',
            '        }',
            '        th {',
            '            background: #333;',
            '            color: #6b9eff;',
            '            font-weight: 600;',
            '        }',
            '        tr:last-child td {',
            '            border-bottom: none;',
            '        }',
            '        tr:hover {',
            '            background: #2f2f2f;',
            '        }',
            '        .complete-msg {',
            '            background: #2a4a2a;',
            '            border-left: 4px solid #4ade80;',
            '            padding: 15px;',
            '            margin-top: 30px;',
            '            border-radius: 4px;',
            '        }',
            '    </style>',
            '</head>',
            '<body>',
            '    <h1>Zen Browser Setup Guide</h1>',
            '    <p>Your declarative configuration has been applied! Complete these final manual steps:</p>',
        ]

        # Add manual configuration sections
        workspace_section_start = [
            '    <h2>1. Configure Workspaces</h2>',
            '    <div class="step">',
        ]

        if workspaces:
            workspace_section_start.extend([
                f'        <p>You have <strong>{len(workspaces)}</strong> workspace(s) configured. Set them up as follows:</p>',
                '        <h3>Configured Workspaces</h3>',
                '        <table>',
                '            <thead>',
                '                <tr>',
                '                    <th>Name</th>',
                '                    <th>Icon</th>',
                '                    <th>Default Container</th>',
                '                </tr>',
                '            </thead>',
                '            <tbody>',
            ])

            for ws in workspaces:
                name = ws.get('name', 'Unnamed')
                icon = ws.get('icon', 'Not specified')
                container = ws.get('default_container', 'None')
                workspace_section_start.extend([
                    '                <tr>',
                    f'                    <td><strong>{name}</strong></td>',
                    f'                    <td>{icon}</td>',
                    f'                    <td>{container}</td>',
                    '                </tr>',
                ])

            workspace_section_start.extend([
                '            </tbody>',
                '        </table>',
            ])
        else:
            workspace_section_start.append('        <p>No workspaces configured in your config file.</p>')

        workspace_section_start.extend([
            '        <h3>Setup Instructions</h3>',
            '        <ol>',
            '            <li>Click the <strong>Workspaces</strong> button in the sidebar (or press the keyboard shortcut)</li>',
            '            <li>Click <strong>Create New Workspace</strong></li>',
            '            <li>Name the workspace according to the table above</li>',
            '            <li>Right-click the workspace icon to change it (select the icon from the table)</li>',
            '            <li>If specified, set the default container for the workspace:',
            '                <ul>',
            '                    <li>Open a new tab in the workspace</li>',
            '                    <li>Click the container icon in the address bar</li>',
            '                    <li>Select the appropriate container</li>',
            '                    <li>Future tabs in this workspace will use this container by default</li>',
            '                </ul>',
            '            </li>',
            '            <li>Repeat for each workspace in your configuration</li>',
            '            <li>Organize your existing tabs by dragging them to the appropriate workspaces</li>',
            '        </ol>',
            '    </div>',
        ])

        html_parts.extend(workspace_section_start)

        # Essential tabs section
        essentials_section = [
            '    <h2>2. Pin Essential Tabs</h2>',
            '    <div class="step">',
        ]

        # Collect all essentials from workspaces
        workspace_essentials = []
        for ws in workspaces:
            ws_name = ws.get('name', 'Unnamed')
            ws_essentials = ws.get('essentials', [])
            if ws_essentials:
                workspace_essentials.append({
                    'name': ws_name,
                    'essentials': ws_essentials
                })

        if workspace_essentials:
            essentials_section.extend([
                f'        <p>You have essential tabs configured across <strong>{len(workspace_essentials)}</strong> workspace(s).</p>',
                '        <h3>Essential Tabs by Workspace</h3>',
            ])

            # Create a table for each workspace with essentials
            for ws_data in workspace_essentials:
                ws_name = ws_data['name']
                essentials = ws_data['essentials']

                essentials_section.extend([
                    f'        <h4>{ws_name} Workspace</h4>',
                    '        <table>',
                    '            <thead>',
                    '                <tr>',
                    '                    <th>#</th>',
                    '                    <th>URL</th>',
                    '                </tr>',
                    '            </thead>',
                    '            <tbody>',
                ])

                for idx, url in enumerate(essentials, 1):
                    essentials_section.extend([
                        '                <tr>',
                        f'                    <td>{idx}</td>',
                        f'                    <td><a href="{url}" target="_blank">{url}</a></td>',
                        '                </tr>',
                    ])

                essentials_section.extend([
                    '            </tbody>',
                    '        </table>',
                ])

            essentials_section.extend([
                '        <h3>Setup Instructions</h3>',
                '        <p><strong>Important:</strong> Essential tabs must be pinned in the correct workspace.</p>',
                '        <ol>',
                '            <li>Switch to the <strong>first workspace</strong> listed above (click on it in the sidebar)</li>',
                '            <li>Click on each URL in the table above to open it in a new tab</li>',
                '            <li>Once all tabs are open, right-click each tab</li>',
                '            <li>Select <strong>Pin as Essential</strong> from the context menu</li>',
                '            <li>The tab will become an essential tab (pinned and always visible)</li>',
                '            <li>Repeat this process for each workspace listed above</li>',
                '        </ol>',
                '        <p><em>Tip: Essential tabs will appear at the top of your tab bar and persist across sessions.</em></p>',
            ])
        else:
            essentials_section.extend([
                '        <p>No essential tabs configured in your workspaces.</p>',
                '        <p>To add essential tabs:</p>',
                '        <ol>',
                '            <li>Open the tabs you want to pin</li>',
                '            <li>Right-click each tab</li>',
                '            <li>Select <strong>Pin as Essential</strong></li>',
                '        </ol>',
            ])

        essentials_section.append('    </div>')
        html_parts.extend(essentials_section)

        html_parts.extend([
            '    <div class="complete-msg">',
            '        <strong>All done?</strong> You can delete this file once you\'ve completed all steps.',
            '    </div>',
            '</body>',
            '</html>',
        ])

        # Write the HTML file
        guide_path.write_text('\n'.join(html_parts))
        return guide_path

    def _bootstrap_install_sections(self, zen_dir: Path, profile_path: Path, zen_install_path: Path) -> bool:
        """
        Bootstrap Install sections by launching Zen Browser briefly.

        Returns True if Install sections were created, False otherwise.
        """
        profiles_ini = zen_dir / "profiles.ini"

        # Check if Install sections already exist
        if profiles_ini.exists():
            content = profiles_ini.read_text()
            if '[Install' in content:
                return False  # Install sections already exist

        print("\nNo installation hash found. Launching Zen Browser to generate it...")
        print("(This will take a few seconds)")

        import signal
        import time

        try:
            # Launch Zen Browser in the background
            proc = subprocess.Popen(
                ['zen-browser'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                preexec_fn=os.setpgrp  # Create new process group
            )

            # Wait for Install sections to appear (max 15 seconds)
            max_wait = 15
            interval = 0.5
            waited = 0

            while waited < max_wait:
                time.sleep(interval)
                waited += interval

                if profiles_ini.exists():
                    content = profiles_ini.read_text()
                    if '[Install' in content:
                        print(f"Installation hash detected after {waited:.1f}s")
                        break

            # Kill Zen Browser
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                proc.wait(timeout=5)
            except:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except:
                    pass

            print("Zen Browser closed")

            # Verify Install sections were created
            if profiles_ini.exists():
                content = profiles_ini.read_text()
                if '[Install' in content:
                    return True

            print("Warning: Installation hash not detected. You may need to run the script again after launching Zen.")
            return False

        except Exception as e:
            print(f"Warning: Could not launch Zen Browser automatically: {e}")
            print("Please launch Zen Browser manually, close it, then run this script again.")
            return False

    def apply(self) -> None:
        """Apply configuration to Zen Browser profile."""
        print("=" * 60)
        print("Zen Browser Configuration Application")
        print("=" * 60)

        # Detect paths
        zen_install_path, profile_path = self.detect_zen_paths()
        zen_dir = self.home / ".zen"

        print(f"\nZen installation: {zen_install_path}")
        print(f"Profile path: {profile_path}")
        print()

        # Generate and install configuration
        self.generate_user_js(profile_path)
        self.generate_policies_json(zen_install_path)
        self.create_search_engine_bookmarks(profile_path)

        # Check if we need to bootstrap Install sections
        bootstrapped = self._bootstrap_install_sections(zen_dir, profile_path, zen_install_path)

        if bootstrapped:
            print("\nUpdating Install sections to use configured profile...")
            # Re-run profile registration to update Install sections
            profile_name = self.config.get("profile", {}).get("name", "default")
            self._register_profile_in_ini(zen_dir, profile_name, profile_path, zen_install_path, update_only=True)
            self._update_installs_ini(zen_dir, profile_path, zen_install_path)

        # Generate and open setup guide FIRST (so mod tabs appear on top)
        print()
        workspaces = self.config.get("workspaces", [])
        guide_path = self.generate_setup_guide(zen_dir, workspaces)
        print(f"Generated setup guide: {guide_path}")

        # Open setup guide in browser
        try:
            subprocess.Popen(
                ['zen-browser', str(guide_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            print("Opened setup guide in browser")
        except Exception as e:
            print(f"Warning: Could not open setup guide automatically: {e}")
            print(f"Please open manually: {guide_path}")

        # Open mod installation pages (will appear on top of setup guide)
        print()
        self.install_zen_mods(profile_path)

        print()
        print("=" * 60)
        print("Configuration applied successfully!")
        print("=" * 60)
        print("\nNext steps:")
        print("1. Complete the manual steps in the setup guide")
        print("2. Extensions will be automatically installed on first launch")
        print("3. Preferences from user.js will be applied")
        print("4. Custom search engines will be available (type keyword + space + search term)")
        print("\nNote: Some settings may require a browser restart to take effect.")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Apply declarative configuration to Zen Browser"
    )
    parser.add_argument(
        "config",
        nargs="?",
        default="config.yaml",
        help="Path to configuration YAML file (default: config.yaml)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes"
    )

    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Error: Configuration file not found: {config_path}")
        sys.exit(1)

    if args.dry_run:
        print("Dry run mode not yet implemented")
        sys.exit(1)

    zen_config = ZenConfig(config_path)
    zen_config.apply()


if __name__ == "__main__":
    main()
