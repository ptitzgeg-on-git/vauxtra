#!/usr/bin/env python3
"""Test new CRUD endpoints for DNS records and proxy hosts."""

import requests
import json
import time
import os

# Use backend API directly by default. On some Windows setups, "localhost"
# resolves to IPv6 (::1) and may hit a different listener than uvicorn.
BASE_URL = os.environ.get("VAUXTRA_API_BASE", "http://127.0.0.1:8888/api")

def test_dns_records():
    """Test DNS record CRUD."""
    print("\n🧪 DNS Record CRUD Test (AdGuard)")
    print("=" * 70)
    
    provider_id = 1  # AdGuard
    
    # CREATE
    print("1️⃣ CREATE DNS Record")
    create_r = requests.post(
        f"{BASE_URL}/providers/{provider_id}/dns-records",
        json={"domain": "vauxtra-test.lab.test", "answer": "10.99.99.99"},
        timeout=10
    )
    print(f"   POST /providers/{provider_id}/dns-records")
    print(f"   Status: {create_r.status_code}")
    if create_r.status_code != 201:
        print(f"   ❌ Response: {create_r.text[:200]}")
        return False
    print(f"   ✅ Created: {create_r.json()}")
    
    time.sleep(0.5)
    
    # READ
    print("\n2️⃣ READ DNS Records")
    list_r = requests.get(f"{BASE_URL}/providers/{provider_id}/dns-records", timeout=10)
    print(f"   GET /providers/{provider_id}/dns-records")
    print(f"   Status: {list_r.status_code}")
    if list_r.status_code != 200:
        print(f"   ❌ Response: {list_r.text[:200]}")
        return False
    records = list_r.json().get("records", [])
    found = any(r.get("domain") == "vauxtra-test.lab.test" for r in records)
    print(f"   Total records: {len(records)}")
    print(f"   Found our record: {'✅' if found else '❌'}")
    
    # DELETE
    print("\n3️⃣ DELETE DNS Record")
    delete_r = requests.delete(
        f"{BASE_URL}/providers/{provider_id}/dns-records/vauxtra-test.lab.test?answer=10.99.99.99",
        timeout=10
    )
    print(f"   DELETE /providers/{provider_id}/dns-records/vauxtra-test.lab.test")
    print(f"   Status: {delete_r.status_code}")
    if delete_r.status_code not in [200, 204]:
        print(f"   ❌ Response: {delete_r.text[:200]}")
        return False
    print(f"   ✅ Deleted")
    
    return True


def test_proxy_hosts():
    """Test proxy host CRUD."""
    print("\n\n🧪 Proxy Host CRUD Test (NPM)")
    print("=" * 70)
    
    provider_id = 4  # NPM
    
    # CREATE
    print("1️⃣ CREATE Proxy Host")
    create_r = requests.post(
        f"{BASE_URL}/providers/{provider_id}/proxy-hosts",
        json={
            "domain_names": ["vauxtra-test-npm.lab.test"],
            "forward_host": "whoami",
            "forward_port": 80,
            "scheme": "http"
        },
        timeout=10
    )
    print(f"   POST /providers/{provider_id}/proxy-hosts")
    print(f"   Status: {create_r.status_code}")
    if create_r.status_code not in [201, 200]:
        print(f"   ❌ Response: {create_r.text[:200]}")
        return False
    result = create_r.json()
    print(f"   ✅ Created: {result}")
    host_id = result.get("result", {}).get("id")
    
    time.sleep(0.5)
    
    # READ
    print("\n2️⃣ READ Proxy Hosts")
    list_r = requests.get(f"{BASE_URL}/providers/{provider_id}/proxy-hosts", timeout=10)
    print(f"   GET /providers/{provider_id}/proxy-hosts")
    print(f"   Status: {list_r.status_code}")
    if list_r.status_code != 200:
        print(f"   ❌ Response: {list_r.text[:200]}")
        return False
    hosts = list_r.json().get("hosts", [])
    found = any(
        "vauxtra-test-npm" in str(h.get("domain_names", [])) or
        "vauxtra-test-npm" in str(h.get("domains", []))
        for h in hosts
    )
    print(f"   Total hosts: {len(hosts)}")
    print(f"   Found our host: {'✅' if found else '❌'}")
    
    if not host_id:
        # Try to find it
        for h in hosts:
            if (
                "vauxtra-test-npm" in str(h.get("domain_names", [])) or
                "vauxtra-test-npm" in str(h.get("domains", []))
            ):
                host_id = h.get("id")
                break
    
    # DELETE
    if host_id:
        print(f"\n3️⃣ DELETE Proxy Host (ID: {host_id})")
        delete_r = requests.delete(
            f"{BASE_URL}/providers/{provider_id}/proxy-hosts/{host_id}",
            timeout=10
        )
        print(f"   DELETE /providers/{provider_id}/proxy-hosts/{host_id}")
        print(f"   Status: {delete_r.status_code}")
        if delete_r.status_code not in [200, 204]:
            print(f"   ❌ Response: {delete_r.text[:200]}")
            return False
        print(f"   ✅ Deleted")
    else:
        print(f"   ⚠️ Could not find host ID for deletion")
    
    return True


def main():
    print("\n📦 Testing New CRUD Endpoints")
    print("=" * 70)
    
    dns_ok = test_dns_records()
    npm_ok = test_proxy_hosts()
    
    print("\n" + "=" * 70)
    print("📊 Summary:")
    print(f"  DNS Records (AdGuard): {'✅ PASS' if dns_ok else '❌ FAIL'}")
    print(f"  Proxy Hosts (NPM):     {'✅ PASS' if npm_ok else '❌ FAIL'}")
    
    if dns_ok and npm_ok:
        print("\n✅ All CRUD endpoints working! Vauxtra is now independent!")
    else:
        print("\n❌ Some tests failed")
    
    return dns_ok and npm_ok

if __name__ == "__main__":
    import sys
    sys.exit(0 if main() else 1)
