#!/usr/bin/env python3
"""
generate_master_key.py — run ONCE to create the master encryption key.

This key encrypts/decrypts every tenant's secrets (Kite credentials, WhatsApp
tokens, per-tenant DB passwords) in tenant_config. It must live in .env on
the VM and NOWHERE else — not in the database, not in any file that gets
committed or shared.

If this key is ever lost, every encrypted value in tenant_config becomes
permanently unrecoverable (that's the point of encryption — treat this key
with the same care as your Oracle wallet password).
"""
from cryptography.fernet import Fernet

if __name__ == '__main__':
    key = Fernet.generate_key().decode()
    print("Generated a new master encryption key:\n")
    print(f"  {key}\n")
    print("Add this line to /home/ubuntu/.env :\n")
    print(f"  MASTER_ENCRYPTION_KEY={key}\n")
    print("Do NOT run this script again unless you intend to re-encrypt every")
    print("existing tenant's secrets with a new key (they will otherwise become")
    print("undecryptable).")
