"""Regression tests v2 — covers backup encryption round-trip, auth change-password,
services bulk actions, tag/environment updates, and i18n locale file integrity.

All tests run in an isolated in-memory SQLite database (inherited from IsolatedDBTestCase).
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

import app.auth as auth
from app import models
from app.api import auth as auth_api
from app.limiter import limiter as _app_limiter
from app.api import backup as backup_api
from app.api import environments as environments_api
from app.api import services as services_api
from app.api import tags as tags_api
from app.api.backup import RestoreRequest, SecureBackupRequest
from app.auth import hash_password


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _request(method: str = "GET", path: str = "/") -> Request:
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [],
    }
    return Request(scope)


# ---------------------------------------------------------------------------
# Base test case — isolated DB + auth disabled
# ---------------------------------------------------------------------------

class IsolatedDBTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_db_path = models.DB_PATH
        self._orig_data_dir = models.DATA_DIR

        import app.db as _app_db
        self._orig_db_db_path = _app_db.DB_PATH
        self._orig_db_data_dir = _app_db.DATA_DIR

        test_db_path = os.path.join(self._tmpdir.name, "dns_manager.v2.test.db")
        models.DATA_DIR = self._tmpdir.name
        models.DB_PATH = test_db_path
        _app_db.DATA_DIR = self._tmpdir.name
        _app_db.DB_PATH = test_db_path
        models.init_db()

        # Disable auth globally for most tests
        self._auth_patch = patch.object(auth, "APP_PASSWORD", "")
        self._auth_patch.start()

        # Disable rate limiting for all tests — slowapi state is shared across tests
        # Disable rate limiting for all tests — slowapi state is shared across tests.
        # The sync_wrapper reads request.state.view_rate_limit after _check_request_limit,
        # so our no-op mock must set that attribute to avoid AttributeError.
        def _no_rate_limit(request, *_args, **_kwargs):
            request.state.view_rate_limit = None

        self._limiter_patch = patch.object(
            _app_limiter, "_check_request_limit", _no_rate_limit
        )
        self._limiter_patch.start()

    def tearDown(self) -> None:
        self._limiter_patch.stop()
        self._auth_patch.stop()

        import app.db as _app_db
        models.DB_PATH = self._orig_db_path
        models.DATA_DIR = self._orig_data_dir
        _app_db.DB_PATH = self._orig_db_db_path
        _app_db.DATA_DIR = self._orig_db_data_dir
        self._tmpdir.cleanup()

    # ---- DB helpers ----

    def _insert_provider(self, name: str = "NPM", password: str = "") -> int:
        from app.config import encrypt_secret
        conn = models.get_db()
        try:
            enc = encrypt_secret(password) if password else ""
            cur = conn.execute(
                "INSERT INTO providers (name, type, url, username, password, extra, enabled) "
                "VALUES (?,?,?,?,?,?,?)",
                (name, "npm", "http://npm.local:81", "admin", enc, "{}", 1),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def _insert_service(self, subdomain: str = "app", domain: str = "example.com") -> int:
        conn = models.get_db()
        try:
            cur = conn.execute(
                "INSERT INTO services (subdomain, domain, target_ip, target_port, "
                "forward_scheme, enabled, status) VALUES (?,?,?,?,?,?,?)",
                (subdomain, domain, "127.0.0.1", 8080, "http", 1, "unknown"),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def _count(self, table: str) -> int:
        conn = models.get_db()
        try:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        finally:
            conn.close()


# ===========================================================================
# 1. Backup — secure export + restore round-trip
# ===========================================================================

class BackupSecureRoundTripTests(IsolatedDBTestCase):

    def test_secure_export_has_secrets_included_flag(self) -> None:
        """POST /api/backup/secure must return a JSON body with secrets_included=True."""
        self._insert_provider("NPM-secure", password="s3cr3t-pass")

        with patch.object(backup_api, "require_auth", lambda _req, scope=None: None), \
             patch.object(backup_api, "limiter") as mock_limiter:
            mock_limiter.limit = lambda *a, **kw: (lambda f: f)

            response = backup_api.export_backup_secure(
                _request("POST", "/api/backup/secure"),
                SecureBackupRequest(passphrase="SuperSecret99"),
            )

        body = json.loads(response.body)
        self.assertTrue(body["secrets_included"])
        self.assertIn("encryption_salt", body)
        self.assertIsInstance(body["encryption_salt"], str)
        self.assertGreater(len(body["encryption_salt"]), 0)
        # Passwords must be encrypted (not empty, not plaintext)
        for p in body["providers"]:
            if p["name"] == "NPM-secure":
                self.assertNotEqual(p["password"], "s3cr3t-pass")
                self.assertNotEqual(p["password"], "")

    def test_secure_export_rejects_short_passphrase(self) -> None:
        """Passphrases shorter than 8 chars must be rejected with 400."""
        with patch.object(backup_api, "require_auth", lambda _req, scope=None: None), \
             patch.object(backup_api, "limiter") as mock_limiter:
            mock_limiter.limit = lambda *a, **kw: (lambda f: f)

            with self.assertRaises(HTTPException) as ctx:
                backup_api.export_backup_secure(
                    _request("POST", "/api/backup/secure"),
                    SecureBackupRequest(passphrase="short"),
                )
        self.assertEqual(ctx.exception.status_code, 400)

    def test_secure_restore_round_trip_decrypts_passwords(self) -> None:
        """Full cycle: secure export → wipe DB → restore → provider password decrypts correctly."""
        pid = self._insert_provider("CFProvider", password="cloudflare-token-abc")

        with patch.object(backup_api, "require_auth", lambda _req, scope=None: None), \
             patch.object(backup_api, "limiter") as mock_limiter:
            mock_limiter.limit = lambda *a, **kw: (lambda f: f)

            export_response = backup_api.export_backup_secure(
                _request("POST", "/api/backup/secure"),
                SecureBackupRequest(passphrase="MyBackupPass1"),
            )

        backup_data = json.loads(export_response.body)

        # Wipe the provider so we can confirm restore brings it back
        conn = models.get_db()
        conn.execute("DELETE FROM providers")
        conn.commit()
        conn.close()
        self.assertEqual(self._count("providers"), 0)

        with patch.object(backup_api, "require_auth", lambda _req, scope=None: None), \
             patch.object(backup_api, "limiter") as mock_limiter:
            mock_limiter.limit = lambda *a, **kw: (lambda f: f)

            result = backup_api.import_backup(
                _request("POST", "/api/restore"),
                RestoreRequest(backup=backup_data, passphrase="MyBackupPass1"),
            )

        self.assertTrue(result["ok"])
        self.assertEqual(self._count("providers"), 1)

        # Verify the decrypted password is correct
        from app.config import decrypt_secret
        conn = models.get_db()
        row = conn.execute("SELECT password FROM providers WHERE name='CFProvider'").fetchone()
        conn.close()
        decrypted = decrypt_secret(row["password"])
        self.assertEqual(decrypted, "cloudflare-token-abc")

    def test_restore_requires_passphrase_for_encrypted_backup(self) -> None:
        """Restoring a secrets_included backup without passphrase must return 400."""
        fake_backup = {
            "version": "7",
            "secrets_included": True,
            "encryption_salt": "dGVzdHNhbHQxMjM0NTY3OA==",
            "providers": [],
            "services": [],
            "tags": [],
            "service_tags": [],
            "service_push_targets": [],
            "environments": [],
            "service_environments": [],
            "domains": [],
            "webhooks": [],
            "service_alerts": [],
            "settings": [],
            "docker_endpoints": [],
        }

        with patch.object(backup_api, "require_auth", lambda _req, scope=None: None), \
             patch.object(backup_api, "limiter") as mock_limiter:
            mock_limiter.limit = lambda *a, **kw: (lambda f: f)

            with self.assertRaises(HTTPException) as ctx:
                backup_api.import_backup(
                    _request("POST", "/api/restore"),
                    RestoreRequest(backup=fake_backup, passphrase=""),
                )
        self.assertEqual(ctx.exception.status_code, 400)

    def test_restore_rejects_wrong_passphrase(self) -> None:
        """Restoring with wrong passphrase must raise 400 (decryption failure)."""
        self._insert_provider("MyProvider", password="real-secret")

        with patch.object(backup_api, "require_auth", lambda _req, scope=None: None), \
             patch.object(backup_api, "limiter") as mock_limiter:
            mock_limiter.limit = lambda *a, **kw: (lambda f: f)

            export_response = backup_api.export_backup_secure(
                _request("POST", "/api/backup/secure"),
                SecureBackupRequest(passphrase="CorrectPassw0rd"),
            )

        backup_data = json.loads(export_response.body)

        with patch.object(backup_api, "require_auth", lambda _req, scope=None: None), \
             patch.object(backup_api, "limiter") as mock_limiter:
            mock_limiter.limit = lambda *a, **kw: (lambda f: f)

            with self.assertRaises(HTTPException) as ctx:
                backup_api.import_backup(
                    _request("POST", "/api/restore"),
                    RestoreRequest(backup=backup_data, passphrase="WrongPassw0rd"),
                )
        self.assertEqual(ctx.exception.status_code, 400)

    def test_plain_backup_restore_clears_passwords(self) -> None:
        """Plain backup (GET /backup) strips passwords; restore re-imports with empty passwords."""
        self._insert_provider("NPM-plain", password="should-be-stripped")

        with patch.object(backup_api, "require_auth", lambda _req, scope=None: None):
            export_response = backup_api.export_backup(
                _request("GET", "/api/backup"),
            )

        backup_data = json.loads(export_response.body)
        self.assertFalse(backup_data["secrets_included"])
        for p in backup_data["providers"]:
            self.assertEqual(p["password"], "", "Plain backup must strip all passwords")

        # Restore plain backup
        with patch.object(backup_api, "require_auth", lambda _req, scope=None: None), \
             patch.object(backup_api, "limiter") as mock_limiter:
            mock_limiter.limit = lambda *a, **kw: (lambda f: f)

            result = backup_api.import_backup(
                _request("POST", "/api/restore"),
                RestoreRequest(backup=backup_data, passphrase=""),
            )
        self.assertTrue(result["ok"])

        conn = models.get_db()
        row = conn.execute("SELECT password FROM providers WHERE name='NPM-plain'").fetchone()
        conn.close()
        self.assertEqual(row["password"], "")


# ===========================================================================
# 2. Auth — change-password
# ===========================================================================

class ChangePasswordTests(IsolatedDBTestCase):

    def _set_db_password(self, plaintext: str) -> None:
        """Store a PBKDF2 hash of plaintext in the settings table."""
        hashed = hash_password(plaintext)
        conn = models.get_db()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('app_password_hash', ?)",
                (hashed,),
            )
            conn.commit()
        finally:
            conn.close()

    def test_change_password_success(self) -> None:
        """Correct current password + valid new password → ok=True and hash updated."""
        self._set_db_password("OldPassword1")

        with patch.object(auth_api, "require_auth", lambda _req, scope=None: None), \
             patch.object(auth_api, "limiter") as mock_limiter:
            mock_limiter.limit = lambda *a, **kw: (lambda f: f)

            result = auth_api.change_password(
                _request("POST", "/api/auth/change-password"),
                auth_api.ChangePasswordBody(
                    current_password="OldPassword1",
                    new_password="NewPassword2",
                ),
            )

        self.assertEqual(result, {"ok": True})

        # The new password must now verify correctly
        self.assertTrue(auth.check_password("NewPassword2"))
        self.assertFalse(auth.check_password("OldPassword1"))

    def test_change_password_wrong_current(self) -> None:
        """Wrong current password → 401."""
        self._set_db_password("RealPassword1")

        with patch.object(auth_api, "require_auth", lambda _req, scope=None: None), \
             patch.object(auth_api, "limiter") as mock_limiter:
            mock_limiter.limit = lambda *a, **kw: (lambda f: f)

            with self.assertRaises(HTTPException) as ctx:
                auth_api.change_password(
                    _request("POST", "/api/auth/change-password"),
                    auth_api.ChangePasswordBody(
                        current_password="WrongPassword1",
                        new_password="SomethingNew2",
                    ),
                )
        self.assertEqual(ctx.exception.status_code, 401)

    def test_change_password_new_too_short(self) -> None:
        """New password shorter than 8 chars → 400."""
        self._set_db_password("MyPassword1")

        with patch.object(auth_api, "require_auth", lambda _req, scope=None: None), \
             patch.object(auth_api, "limiter") as mock_limiter:
            mock_limiter.limit = lambda *a, **kw: (lambda f: f)

            with self.assertRaises(HTTPException) as ctx:
                auth_api.change_password(
                    _request("POST", "/api/auth/change-password"),
                    auth_api.ChangePasswordBody(
                        current_password="MyPassword1",
                        new_password="short",
                    ),
                )
        self.assertEqual(ctx.exception.status_code, 400)


# ===========================================================================
# 3. Services — bulk actions
# ===========================================================================

class ServicesBulkActionTests(IsolatedDBTestCase):

    def test_bulk_enable(self) -> None:
        """Bulk enable must update enabled=1 for all target service IDs."""
        sid1 = self._insert_service("svc1", "a.com")
        sid2 = self._insert_service("svc2", "b.com")

        # Disable both first
        conn = models.get_db()
        conn.execute(f"UPDATE services SET enabled=0 WHERE id IN ({sid1},{sid2})")
        conn.commit()
        conn.close()

        with patch.object(services_api, "require_auth", lambda _req, scope=None: None):
            result = services_api.bulk_action(
                services_api._BulkActionBody(ids=[sid1, sid2], action="enable"),
                _request("POST", "/api/services/bulk"),
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["affected"], 2)

        conn = models.get_db()
        rows = conn.execute(
            f"SELECT enabled FROM services WHERE id IN ({sid1},{sid2})"
        ).fetchall()
        conn.close()
        self.assertTrue(all(r["enabled"] == 1 for r in rows))

    def test_bulk_disable(self) -> None:
        """Bulk disable must update enabled=0 for all target service IDs."""
        sid1 = self._insert_service("svc3", "c.com")
        sid2 = self._insert_service("svc4", "d.com")

        with patch.object(services_api, "require_auth", lambda _req, scope=None: None):
            result = services_api.bulk_action(
                services_api._BulkActionBody(ids=[sid1, sid2], action="disable"),
                _request("POST", "/api/services/bulk"),
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["affected"], 2)

        conn = models.get_db()
        rows = conn.execute(
            f"SELECT enabled FROM services WHERE id IN ({sid1},{sid2})"
        ).fetchall()
        conn.close()
        self.assertTrue(all(r["enabled"] == 0 for r in rows))

    def test_bulk_delete(self) -> None:
        """Bulk delete must remove services that have no external provider records."""
        sid = self._insert_service("del-svc", "del.com")

        with patch.object(services_api, "require_auth", lambda _req, scope=None: None):
            result = services_api.bulk_action(
                services_api._BulkActionBody(ids=[sid], action="delete"),
                _request("POST", "/api/services/bulk"),
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["affected"], 1)
        self.assertEqual(self._count("services"), 0)

    def test_bulk_invalid_action_returns_400(self) -> None:
        """Unknown bulk action must return 400."""
        sid = self._insert_service("x", "x.com")

        with patch.object(services_api, "require_auth", lambda _req, scope=None: None):
            with self.assertRaises(HTTPException) as ctx:
                services_api.bulk_action(
                    services_api._BulkActionBody(ids=[sid], action="explode"),
                    _request("POST", "/api/services/bulk"),
                )
        self.assertEqual(ctx.exception.status_code, 400)

    def test_bulk_empty_ids_returns_zero_affected(self) -> None:
        """Empty ids list must return affected=0 without error."""
        with patch.object(services_api, "require_auth", lambda _req, scope=None: None):
            result = services_api.bulk_action(
                services_api._BulkActionBody(ids=[], action="enable"),
                _request("POST", "/api/services/bulk"),
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["affected"], 0)


# ===========================================================================
# 4. Tags — CRUD + update
# ===========================================================================

class TagUpdateTests(IsolatedDBTestCase):

    def test_create_and_update_tag(self) -> None:
        """Create a tag, then rename and recolor it via PUT /api/tags/{tid}."""
        with patch.object(tags_api, "require_auth", lambda _req, scope=None: None):
            created = tags_api.create_tag(
                _request("POST", "/api/tags"),
                tags_api.TagIn(name="oldname", color="blue"),
            )

        tid = created["id"]

        with patch.object(tags_api, "require_auth", lambda _req, scope=None: None):
            updated = tags_api.update_tag(
                tid,
                _request("PUT", f"/api/tags/{tid}"),
                tags_api.TagIn(name="newname", color="green"),
            )

        self.assertTrue(updated["ok"])

        conn = models.get_db()
        row = conn.execute("SELECT name, color FROM tags WHERE id=?", (tid,)).fetchone()
        conn.close()
        self.assertEqual(row["name"], "newname")
        self.assertEqual(row["color"], "green")

    def test_update_tag_duplicate_name_returns_409(self) -> None:
        """Renaming a tag to an existing name must return 409."""
        with patch.object(tags_api, "require_auth", lambda _req, scope=None: None):
            tags_api.create_tag(_request(), tags_api.TagIn(name="alpha"))
            second = tags_api.create_tag(_request(), tags_api.TagIn(name="beta"))

        with patch.object(tags_api, "require_auth", lambda _req, scope=None: None):
            with self.assertRaises(HTTPException) as ctx:
                tags_api.update_tag(
                    second["id"],
                    _request("PUT", f"/api/tags/{second['id']}"),
                    tags_api.TagIn(name="alpha"),
                )
        self.assertEqual(ctx.exception.status_code, 409)

    def test_update_nonexistent_tag_returns_404(self) -> None:
        """PUT /api/tags/99999 with no such tag must return 404."""
        with patch.object(tags_api, "require_auth", lambda _req, scope=None: None):
            with self.assertRaises(HTTPException) as ctx:
                tags_api.update_tag(
                    99999,
                    _request("PUT", "/api/tags/99999"),
                    tags_api.TagIn(name="ghost"),
                )
        self.assertEqual(ctx.exception.status_code, 404)


# ===========================================================================
# 5. Environments — CRUD + update
# ===========================================================================

class EnvironmentUpdateTests(IsolatedDBTestCase):

    def test_create_and_update_environment(self) -> None:
        """Create an environment, then update its name and color."""
        with patch.object(environments_api, "require_auth", lambda _req, scope=None: None):
            created = environments_api.add_environment(
                _request("POST", "/api/environments"),
                {"name": "staging", "color": "orange"},
            )

        eid = created["id"]

        with patch.object(environments_api, "require_auth", lambda _req, scope=None: None):
            updated = environments_api.update_environment(
                eid,
                _request("PUT", f"/api/environments/{eid}"),
                {"name": "production", "color": "red"},
            )

        self.assertEqual(updated["name"], "production")
        self.assertEqual(updated["color"], "red")

        conn = models.get_db()
        row = conn.execute("SELECT name, color FROM environments WHERE id=?", (eid,)).fetchone()
        conn.close()
        self.assertEqual(row["name"], "production")
        self.assertEqual(row["color"], "red")

    def test_update_environment_invalid_color_defaults_to_blue(self) -> None:
        """An invalid color must fall back to 'blue' without rejecting the request."""
        with patch.object(environments_api, "require_auth", lambda _req, scope=None: None):
            created = environments_api.add_environment(
                _request(), {"name": "test-env", "color": "blue"}
            )
            updated = environments_api.update_environment(
                created["id"],
                _request("PUT", "/api/environments/1"),
                {"name": "test-env", "color": "notacolor"},
            )
        self.assertEqual(updated["color"], "blue")

    def test_create_duplicate_environment_raises(self) -> None:
        """Creating two environments with the same name must raise 409."""
        with patch.object(environments_api, "require_auth", lambda _req, scope=None: None):
            environments_api.add_environment(_request(), {"name": "dev"})
            with self.assertRaises(HTTPException) as ctx:
                environments_api.add_environment(_request(), {"name": "dev"})
        self.assertEqual(ctx.exception.status_code, 409)


# ===========================================================================
# 6. i18n locale files — structural integrity
# ===========================================================================

_LOCALES_DIR = Path(__file__).parent.parent / "frontend" / "src" / "locales"
_SUPPORTED_LANGS = ["en", "fr", "de", "es", "pt", "nl", "ja", "zh"]


class I18nLocaleIntegrityTests(unittest.TestCase):
    """Verify that all locale JSON files are valid and contain the keys from en.json."""

    def _load(self, lang: str) -> dict:
        path = _LOCALES_DIR / f"{lang}.json"
        self.assertTrue(path.exists(), f"Missing locale file: {path}")
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def _flatten(self, obj: dict, prefix: str = "") -> set:
        """Flatten nested dict into dot-notation key set."""
        keys = set()
        for k, v in obj.items():
            full = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                keys |= self._flatten(v, full)
            else:
                keys.add(full)
        return keys

    def test_all_locale_files_are_valid_json(self) -> None:
        """Every locale file must parse as valid JSON without error."""
        for lang in _SUPPORTED_LANGS:
            with self.subTest(lang=lang):
                data = self._load(lang)
                self.assertIsInstance(data, dict, f"{lang}.json root must be a dict")

    def test_all_locales_have_en_keys(self) -> None:
        """Every locale must contain all keys that exist in en.json."""
        en_keys = self._flatten(self._load("en"))
        for lang in _SUPPORTED_LANGS:
            if lang == "en":
                continue
            with self.subTest(lang=lang):
                lang_keys = self._flatten(self._load(lang))
                missing = en_keys - lang_keys
                self.assertEqual(
                    missing,
                    set(),
                    f"{lang}.json is missing keys: {sorted(missing)}",
                )

    def test_no_empty_translation_values(self) -> None:
        """No translation value should be an empty string (indicates untranslated key)."""
        # Only check non-English files — English can have non-empty values as source
        def _check_empty(obj: dict, path: str = "") -> list:
            empties = []
            for k, v in obj.items():
                full = f"{path}.{k}" if path else k
                if isinstance(v, dict):
                    empties.extend(_check_empty(v, full))
                elif isinstance(v, str) and v.strip() == "":
                    empties.append(full)
            return empties

        # Allow missing/empty only in en.json (source); all others should be non-empty
        for lang in _SUPPORTED_LANGS:
            with self.subTest(lang=lang):
                data = self._load(lang)
                empties = _check_empty(data)
                self.assertEqual(
                    empties,
                    [],
                    f"{lang}.json has empty string values: {empties}",
                )

    def test_nav_keys_present(self) -> None:
        """All locales must have nav.* keys used by the Sidebar."""
        required_nav = {"nav.services", "nav.monitoring", "nav.settings", "nav.certificates"}
        for lang in _SUPPORTED_LANGS:
            with self.subTest(lang=lang):
                keys = self._flatten(self._load(lang))
                missing = required_nav - keys
                self.assertEqual(missing, set(), f"{lang}.json missing nav keys: {missing}")

    def test_settings_keys_present(self) -> None:
        """All locales must have settings.* keys used by Settings page."""
        required_settings = {
            "settings.title",
            "settings.language.title",
            "settings.language.contribute_description",
        }
        for lang in _SUPPORTED_LANGS:
            with self.subTest(lang=lang):
                keys = self._flatten(self._load(lang))
                missing = required_settings - keys
                self.assertEqual(missing, set(), f"{lang}.json missing settings keys: {missing}")


if __name__ == "__main__":
    unittest.main()
