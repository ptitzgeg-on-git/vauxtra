#!/usr/bin/env python3
"""
Setup script: Register all 4 lab providers in Vauxtra via API.
Run this to skip the Setup wizard and populate providers.
"""

import requests
import json
import time
from typing import Dict, Any

BASE_URL = "http://localhost:5173/api"  # Frontend proxy to backend

PROVIDERS = [
    {
        "name": "AdGuard (Lab)",
        "type": "adguard",
        "url": "http://localhost:13000",
        "username": "admin",
        "password": "adminadmin",
    },
    {
        "name": "Pi-hole (Lab)",
        "type": "pihole",
        "url": "http://localhost:18081",
        "username": "",  # Pi-hole doesn't use username for basic auth
        "password": "admin",  # API token or password
    },
    {
        "name": "Technitium (Lab)",
        "type": "technitium",
        "url": "http://localhost:15389",
        "username": "admin",
        "password": "admin",
    },
    {
        "name": "NPM (Lab)",
        "type": "npm",
        "url": "http://localhost:18082",
        "username": "admin@example.com",
        "password": "admin",
    },
]

def add_provider(provider_data: Dict[str, Any]) -> bool:
    """Add a single provider to Vauxtra."""
    try:
        print(f"\n  Adding {provider_data['name']}...", end=" ", flush=True)
        response = requests.post(
            f"{BASE_URL}/providers",
            json=provider_data,
            timeout=10,
        )
        if response.status_code == 201:
            result = response.json()
            print(f"✓ (ID: {result['id']})")
            return True
        else:
            print(f"✗ HTTP {response.status_code}: {response.text[:100]}")
            return False
    except Exception as e:
        print(f"✗ {e}")
        return False

def mark_setup_complete() -> bool:
    """Mark setup as completed."""
    try:
        print("\n  Marking setup as complete...", end=" ", flush=True)
        response = requests.post(
            f"{BASE_URL}/auth/setup-complete",
            timeout=10,
        )
        if response.status_code == 200:
            print("✓")
            return True
        else:
            print(f"✗ HTTP {response.status_code}")
            return False
    except Exception as e:
        print(f"✗ {e}")
        return False

def main():
    """Main entry point."""
    print("\n🔧 Vauxtra Lab Providers Setup")
    print("=" * 50)
    
    # Check auth status
    print("\nChecking auth status...", end=" ", flush=True)
    try:
        response = requests.get(f"{BASE_URL}/auth/me", timeout=5)
        if response.status_code != 200:
            print(f"✗ API unreachable (HTTP {response.status_code})")
            return False
        auth = response.json()
        print(f"✓")
        print(f"  Authenticated: {auth.get('authenticated')}")
        print(f"  Auth required: {auth.get('auth_required')}")
        print(f"  Setup required: {auth.get('setup_required')}")
    except Exception as e:
        print(f"✗ {e}")
        return False
    
    # Add providers
    print("\n📦 Adding providers...")
    success_count = 0
    for provider in PROVIDERS:
        if add_provider(provider):
            success_count += 1
        time.sleep(0.5)
    
    print(f"\n  Result: {success_count}/{len(PROVIDERS)} providers added")
    
    if success_count > 0:
        # Mark setup as complete
        print("\n✅ Setup completion...")
        if mark_setup_complete():
            print("\n🎉 All done! Vauxtra is ready.")
            print("   Navigate to http://localhost:5173 to start using Vauxtra.")
            return True
        else:
            print("\n⚠️  Providers added but setup completion failed.")
            print("   Navigate to http://localhost:5173 - you may need to refresh.")
            return True
    else:
        print("\n❌ No providers were added. Check your API connection.")
        return False

if __name__ == "__main__":
    import sys
    sys.exit(0 if main() else 1)
