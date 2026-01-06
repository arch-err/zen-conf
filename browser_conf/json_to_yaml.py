#!/usr/bin/env python3
"""Helper script to convert browser.uiCustomization.state JSON to YAML format.

Usage:
    1. Go to about:config in Zen Browser
    2. Search for: browser.uiCustomization.state
    3. Copy the JSON value
    4. Run: python json_to_yaml.py
    5. Paste the JSON and press Ctrl+D
    6. Copy the YAML output into your config.yaml under 'toolbar:'
"""

import json
import sys
import yaml


def json_to_yaml():
    """Convert JSON from stdin to YAML on stdout."""
    print("Paste your browser.uiCustomization.state JSON (press Ctrl+D when done):")
    print()

    # Read JSON from stdin
    try:
        json_str = sys.stdin.read()
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON - {e}", file=sys.stderr)
        sys.exit(1)

    # Convert to YAML
    print("\n# Copy this into your config.yaml under 'toolbar:':")
    print("toolbar:")
    yaml_str = yaml.dump(data, default_flow_style=False, sort_keys=False, indent=2)
    # Indent everything by 2 spaces
    for line in yaml_str.splitlines():
        print(f"  {line}")


if __name__ == "__main__":
    json_to_yaml()
