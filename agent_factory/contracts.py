"""Canonical contract identity, independent of any business domain."""

import hashlib
import json


def contract_hash(contract):
    return hashlib.sha256(json.dumps(contract, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
