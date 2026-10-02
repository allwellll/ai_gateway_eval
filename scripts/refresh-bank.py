#!/usr/bin/env python3
"""Refresh the bundled ModelTrace bank; no model API calls."""
import asyncio
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.evaluator import fresh_bank

async def main():
    bank, refreshed = await fresh_bank()
    if not refreshed:
        raise SystemExit('Unable to refresh the bank; the existing file was not changed.')
    path = Path(__file__).resolve().parent.parent / 'backend/modeltrace_data/unified_bank.json'
    path.write_text(json.dumps(bank, ensure_ascii=False), encoding='utf-8')
    print('Updated ModelTrace bank:', bank.get('built_at'))

asyncio.run(main())
