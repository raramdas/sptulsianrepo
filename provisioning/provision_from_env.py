#!/usr/bin/env python3
"""
provision_from_env.py — provisions a new tenant using the Kite credentials
already sitting in .env (ZERODHA_USER_ID, ZERODHA_PASSWORD,
ZERODHA_TOTP_SECRET), instead of re-typing them into interactive prompts.

Only asks for the tenant NAME (not sensitive, not already in .env) and
optional WhatsApp fields. Everything else is pulled straight from .env,
so there's no risk of a typo corrupting your real Kite password/TOTP secret.

Usage:
    python3 provision_from_env.py "Rajesh Ramdas"

Or fully non-interactive (also pass budget):
    python3 provision_from_env.py "Rajesh Ramdas" --budget 200000 --yes
"""
import sys
import os
import argparse
from dotenv import load_dotenv

load_dotenv('/home/ubuntu/.env')

from provision_tenant import provision, DEFAULT_TOTAL_BUDGET


def main():
    parser = argparse.ArgumentParser(description="Provision a tenant using .env Kite credentials")
    parser.add_argument("tenant_name", help="Tenant's display name, e.g. 'Rajesh Ramdas'")
    parser.add_argument("--budget", type=float, default=DEFAULT_TOTAL_BUDGET,
                         help=f"Total portfolio budget (default {DEFAULT_TOTAL_BUDGET:,.0f})")
    parser.add_argument("--whatsapp", default=None, help="WhatsApp number (optional)")
    parser.add_argument("--meta-phone-id", default=None, help="Meta phone number ID (optional)")
    parser.add_argument("--meta-waba-id", default=None, help="Meta WABA ID (optional)")
    parser.add_argument("--meta-token", default=None, help="Meta access token (optional)")
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    args = parser.parse_args()

    # Pull Kite credentials straight from .env — no manual re-typing
    try:
        kite_user_id = os.environ['ZERODHA_USER_ID']
        kite_password = os.environ['ZERODHA_PASSWORD']
        kite_totp_secret = os.environ['ZERODHA_TOTP_SECRET']
    except KeyError as e:
        print(f"Missing {e} in .env — cannot proceed without Kite credentials there.")
        sys.exit(1)

    print("=== Provision Tenant from .env ===\n")
    print(f"Tenant name:     {args.tenant_name}")
    print(f"Kite user ID:    {kite_user_id}  (from .env)")
    print(f"Kite password:   {'*' * len(kite_password)}  (from .env, masked)")
    print(f"Kite TOTP secret: {'*' * len(kite_totp_secret)}  (from .env, masked)")
    print(f"WhatsApp number: {args.whatsapp or '(not set)'}")
    print(f"Total budget:    Rs.{args.budget:,.2f}\n")

    if not args.yes:
        confirm = input("Proceed with these values? [y/N]: ").strip().lower()
        if confirm != 'y':
            print("Cancelled.")
            return

    provision(
        tenant_name=args.tenant_name,
        kite_user_id=kite_user_id,
        kite_password=kite_password,
        kite_totp_secret=kite_totp_secret,
        whatsapp_number=args.whatsapp,
        meta_phone_number_id=args.meta_phone_id,
        meta_waba_id=args.meta_waba_id,
        meta_access_token=args.meta_token,
        total_budget=args.budget,
    )


if __name__ == '__main__':
    main()
