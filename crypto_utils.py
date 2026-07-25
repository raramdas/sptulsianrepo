#!/usr/bin/env python3
"""
crypto_utils.py — encrypt/decrypt helper for tenant secrets (Kite credentials,
WhatsApp tokens, per-tenant DB passwords) before they're stored in
ADMIN.tenant_config.

The master key lives ONLY in .env on the VM (MASTER_ENCRYPTION_KEY) — never
in the database, never in code. Anyone with DB access but not this key sees
only ciphertext.

Uses Fernet (symmetric, authenticated encryption from the `cryptography`
package) — appropriate here since we only ever need to decrypt with the same
key that encrypted (no public/private key exchange needed).
"""
import os
from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv

load_dotenv('/home/ubuntu/.env')


def _get_fernet():
    key = os.environ.get('MASTER_ENCRYPTION_KEY')
    if not key:
        raise RuntimeError(
            "MASTER_ENCRYPTION_KEY not found in .env. "
            "Run generate_master_key.py once and add the printed key to .env first."
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(plaintext):
    """Encrypt a string. Returns None if input is None/empty (so optional
    fields like WhatsApp token stay NULL instead of encrypting an empty string)."""
    if not plaintext:
        return None
    f = _get_fernet()
    return f.encrypt(plaintext.encode()).decode()


def decrypt(ciphertext):
    """Decrypt a string previously produced by encrypt(). Returns None for
    None/empty input, matching encrypt()'s behavior."""
    if not ciphertext:
        return None
    f = _get_fernet()
    try:
        return f.decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        raise ValueError(
            "Could not decrypt value — wrong MASTER_ENCRYPTION_KEY, or the "
            "value was not encrypted with this key."
        )


if __name__ == '__main__':
    # Round-trip self-test (requires MASTER_ENCRYPTION_KEY already in .env)
    original = "SuperSecretPassword123!"
    enc = encrypt(original)
    dec = decrypt(enc)
    print(f"Original:  {original}")
    print(f"Encrypted: {enc}")
    print(f"Decrypted: {dec}")
    assert dec == original, "Round-trip FAILED"
    print("Round-trip OK")
