#!/usr/bin/env python3
"""
spt_scraper.py — scrapes Type/Target/Timeframe/Have Interest from sptulsian.com.

CURRENTLY DISABLED: the OCI VM's IP is blocked by CloudFront on sptulsian.com.
Will be re-enabled once the static IP is whitelisted with SPTulsian (contact
their support with IP 140.245.226.35). Until then this always returns blanks
and process_tip() falls back to AMFI-derived cap classification instead.

Test independently:
    python3 -c "from spt_scraper import scrape_spt_stock; print(scrape_spt_stock('Zee Ent', 'Little Gems'))"
"""
from config import log, SPT_USERNAME, SPT_PASSWORD

# SPTulsian section URL map (kept for when scraping is re-enabled)
SPT_URL_MAP = {
    'little gems':              'https://www.sptulsian.com/m/little-gems',
    'big gems':                 'https://www.sptulsian.com/m/big-gems',
    'short term investments':   'https://www.sptulsian.com/m/short-term-investments',
    'medium term investments':  'https://www.sptulsian.com/m/medium-term-investments',
    'regular income bluechips': 'https://www.sptulsian.com/m/regular-income-bluechips',
    'multibagger stocks':       'https://www.sptulsian.com/m/multibagger-stocks',
}


def get_spt_page():
    """Stub — returns None until IP whitelisting is confirmed with SPTulsian."""
    return None


def scrape_spt_stock(stock_name, category):
    """Stub — returns empty result until scraping is re-enabled."""
    return {'type': '', 'target': '', 'timeframe': '', 'have_interest': ''}


def quit_spt_driver():
    """No-op until SPTulsian scraping is re-enabled."""
    pass


if __name__ == '__main__':
    result = scrape_spt_stock('Zee Ent', 'Little Gems')
    print(f"scrape_spt_stock result (should be blank while disabled): {result}")
