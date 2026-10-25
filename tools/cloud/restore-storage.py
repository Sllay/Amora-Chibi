#!/usr/bin/env python3
"""Run with the Core venv, from repo root; never prints connection credentials."""
import argparse
from pathlib import Path
from amora.storage.backend import backend_from_environment
from amora.storage.recovery import restore_empty_remote

parser = argparse.ArgumentParser(description='Restore audited snapshot to EMPTY remote storage')
parser.add_argument('snapshot', type=Path)
parser.add_argument('--empty-destination', action='store_true', required=True)
args = parser.parse_args()
backend = backend_from_environment()
if not backend.capabilities.remote:
    parser.error('select AMORA_STORAGE_BACKEND=libsql explicitly')
restore_empty_remote(backend, args.snapshot)
print('Snapshot and assets restored; validate a new Core before switching clients.')
