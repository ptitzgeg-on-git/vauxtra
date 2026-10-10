import base64
import hashlib
import logging
import os
import secrets

from cryptography.fernet import Fernet

APP_VERSION = os.environ.get("APP_VERSION", "dev")

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
        _restrict_key_file(key_file)
        with open(key_file) as fh:
            stored = fh.read().strip()
        if stored:
            return stored
        os.remove(key_file)
    generated = secrets.token_hex(32)
    # Written in full to a private temporary file, then linked into place. Creating the key
    # file itself with O_EXCL and writing afterwards left a window where it existed and was
    # empty: a second worker starting then read "", fell back to its own key, and the two
    # workers signed sessions and encrypted secrets under different keys.
    # `os.link` publishes the complete file or fails because one is already there.
    tmp = f"{key_file}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(generated)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(tmp, key_file)
        except FileExistsError:
            with open(key_file) as fh:
                return fh.read().strip() or generated
        except OSError:
            # A filesystem without hard links: a rename is atomic too, and on the platforms
            # that lack `link` it refuses an existing destination as well.
            try:
                os.rename(tmp, key_file)
            except FileExistsError:
                with open(key_file) as fh:
                    return fh.read().strip() or generated
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return generated


def _restrict_key_file(path: str) -> None:
    """Take group and other access off a key file written before 0600 was enforced."""
    if os.name != "posix":
        return
    try:
        if os.stat(path).st_mode & 0o077:
            os.chmod(path, 0o600)
    except OSError:
        pass


SECRET_KEY   = _get_or_generate_secret()
APP_PASSWORD = os.environ.get("APP_PASSWORD", "").strip()
HTTPS_ONLY   = os.environ.get("HTTPS_ONLY", "false").strip().lower() in ("1", "true", "yes")
DEBUG        = os.environ.get("DEBUG", "false").strip().lower() in ("1", "true", "yes")

# Fernet key derived from SECRET_KEY (deterministic, persists across restarts)
_raw_key    = hashlib.sha256(SECRET_KEY.encode()).digest()
_fernet_key = base64.urlsafe_b64encode(_raw_key)
fernet      = Fernet(_fernet_key)

logger = logging.getLogger(__name__)


def encrypt_secret(s: str) -> str:
    """Encrypt a string with Fernet."""
    if not s:
        return s
    return fernet.encrypt(s.encode()).decode()


# A Fernet token starts with a 0x80 version byte, which always yields this prefix once
# base64url-encoded. It is what separates "this field was never encrypted" from "this field
# is encrypted under a different key".
_FERNET_PREFIX = "gAAAAA"


def decrypt_secret(s: str) -> str:
    """Decrypt a Fernet string, or return it as-is if it was never encrypted (legacy).

    Both possible failures used to leave here the same way: as the value itself. For a
    password stored in clear before encryption existed, that is the right answer. For a
    token encrypted under a `SECRET_KEY` that is no longer there -- variable lost, file not
    mounted, a fresh key generated at boot -- it handed the *ciphertext* to the provider as
    the password. What the operator saw was every provider failing authentication at once,
    with nothing anywhere naming the cause, and the ciphertext going out over the network
    to a third party.

    That case now yields an empty string and says why in the log.
    """
    if not s:
        return s
    try:
        return fernet.decrypt(s.encode()).decode()
    except Exception:
        if s.startswith(_FERNET_PREFIX):
            logger.error(
                "Cannot decrypt a stored secret: SECRET_KEY does not match the one that "
                "wrote it. Restore the same SECRET_KEY, or re-enter the provider passwords."
            )
            return ""
        return s


# ── Backup encryption with user-provided passphrase ──────────────────────────

def derive_fernet_from_passphrase(passphrase: str, salt: bytes) -> Fernet:
    """Derive a Fernet key from a passphrase using PBKDF2-HMAC-SHA256."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=600_000,  # OWASP recommended minimum
    )
    key = base64.urlsafe_b64encode(kdf.derive(passphrase.encode()))
    return Fernet(key)


def encrypt_for_backup(plaintext: str, passphrase: str, salt: bytes, fernet: Fernet | None = None) -> str:
    """Encrypt a string for backup export using a user passphrase.

    `fernet` is the key already derived for this passphrase and salt: each derivation costs
    600 000 PBKDF2 rounds, and paying them once per secret made a backup take minutes.
    """
    if not plaintext:
        return ""
    f = fernet or derive_fernet_from_passphrase(passphrase, salt)
    return f.encrypt(plaintext.encode()).decode()


def decrypt_from_backup(ciphertext: str, passphrase: str, salt: bytes, fernet: Fernet | None = None) -> str:
    """Decrypt a string from backup using the user passphrase (see `encrypt_for_backup`)."""
    if not ciphertext:
        return ""
    f = fernet or derive_fernet_from_passphrase(passphrase, salt)
    return f.decrypt(ciphertext.encode()).decode()
