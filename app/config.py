import os
import secrets
import base64
import hashlib

from cryptography.fernet import Fernet

APP_VERSION = "1.0.0"

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
DB_PATH  = os.path.join(DATA_DIR, "vauxtra.db")

PROVIDER_TIMEOUT = 10


def _get_or_generate_secret() -> str:
    env_key = os.environ.get("SECRET_KEY", "")
    if env_key and env_key not in ("change-me-in-production", ""):
        return env_key
    key_file = os.path.join(DATA_DIR, ".secret_key")
    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.exists(key_file):
        stored = open(key_file).read().strip()
        if stored:
            return stored
    generated = secrets.token_hex(32)
    with open(key_file, "w") as f:
        f.write(generated)
    try:
        os.chmod(key_file, 0o600)
    except OSError:
        pass
    return generated


SECRET_KEY   = _get_or_generate_secret()
APP_PASSWORD = os.environ.get("APP_PASSWORD", "").strip()
HTTPS_ONLY   = os.environ.get("HTTPS_ONLY", "false").strip().lower() in ("1", "true", "yes")
DEBUG        = os.environ.get("DEBUG", "false").strip().lower() in ("1", "true", "yes")

# Fernet key derived from SECRET_KEY (deterministic, persists across restarts)
_raw_key    = hashlib.sha256(SECRET_KEY.encode()).digest()
_fernet_key = base64.urlsafe_b64encode(_raw_key)
fernet      = Fernet(_fernet_key)


def encrypt_secret(s: str) -> str:
    """Encrypt a string with Fernet."""
    if not s:
        return s
    return fernet.encrypt(s.encode()).decode()


def decrypt_secret(s: str) -> str:
    """Decrypt a Fernet string, or return it as-is if not encrypted (legacy)."""
    if not s:
        return s
    try:
        return fernet.decrypt(s.encode()).decode()
    except Exception:
        return s


# ── Backup encryption with user-provided passphrase ──────────────────────────

def derive_fernet_from_passphrase(passphrase: str, salt: bytes) -> Fernet:
    """Derive a Fernet key from a passphrase using PBKDF2-HMAC-SHA256."""
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=600_000,  # OWASP recommended minimum
    )
    key = base64.urlsafe_b64encode(kdf.derive(passphrase.encode()))
    return Fernet(key)


def encrypt_for_backup(plaintext: str, passphrase: str, salt: bytes) -> str:
    """Encrypt a string for backup export using a user passphrase."""
    if not plaintext:
        return ""
    f = derive_fernet_from_passphrase(passphrase, salt)
    return f.encrypt(plaintext.encode()).decode()


def decrypt_from_backup(ciphertext: str, passphrase: str, salt: bytes) -> str:
    """Decrypt a string from backup using the user passphrase."""
    if not ciphertext:
        return ""
    f = derive_fernet_from_passphrase(passphrase, salt)
    return f.decrypt(ciphertext.encode()).decode()
