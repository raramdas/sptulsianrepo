"""Local browser test server. Synthetic data; never used in deployment."""
import os,sys
from pathlib import Path
os.environ.setdefault('DASH_STATE_DIR','/tmp/capital-preview-state')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from mobile_app import create_app
from fixture import FixtureStore
app=create_app(FixtureStore(),{'preview':'fixture-password'},'fixture-key-not-for-production-12345','/tmp/capital-preview-state',secure=False)
