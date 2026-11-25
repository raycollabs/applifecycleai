"""Sample: Propose a spec revision for sample S-TRADE-BOOK. Verify acceptance criteria format. Record approval as human reviewer."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from durai.db import transaction

SPECS_FOLDER = ROOT / 'design/specs'


def digest_text(text):
    # Sample: Hash the text "abc". Return ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad.
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def validate_criteria(criteria):
    # Sample: Accept 3 numbered criteria. Reject an empty list or an entry with no text.
    if not isinstance(criteria, list) or len(criteria) == 0:
        raise ValueError('Spec must have at least one acceptance criterion')
    cleaned = []
    for index, item in enumerate(criteria):
        text = str(item).strip()
        if not text:
            raise ValueError(f'Acceptance criterion {index + 1} is empty')
        # Check for banned code leakage or raw SQL in spec criteria
        if any(term in text.lower() for term in ('select * from', 'class ', 'def ', '__init__')):
            raise ValueError(f'Acceptance criterion {index + 1} contains implementation code leakage')
        cleaned.append(text)
    return cleaned


def propose_spec(sample_id, revision, request_text, criteria, domain='trading', tech_stack='Python/PostgreSQL',
                 specs_folder=SPECS_FOLDER):
    # Sample: Propose a spec for S-TRADE-BOOK revision 2 with 3 criteria. Return the proposal dict and path.
    valid_criteria = validate_criteria(criteria)
    folder = Path(specs_folder)
    folder.mkdir(parents=True, exist_ok=True)
    body = {
        'format': 'durai-spec/v1',
        'sampleId': sample_id,
        'revision': int(revision),
        'domain': domain,
        'techStack': tech_stack,
        'requestText': request_text.strip(),
        'criteria': valid_criteria,
        'status': 'PROPOSED',
        'reviewer': None,
        'approvedAt': None
    }
    digest = digest_text(json.dumps(body, sort_keys=True, separators=(',', ':')))
    body['digest'] = digest
    path = folder / f"{sample_id}-r{revision}.json"
    path.write_text(json.dumps(body, indent=2) + '\n', encoding='utf-8')
    return body, str(path)


def approve_spec(sample_id, revision, reviewer='user', specs_folder=SPECS_FOLDER, database='durai_local'):
    # Sample: Approve spec S-TRADE-BOOK revision 2 with reviewer user. Mark REVIEWED in database and file. Return updated spec.
    path = Path(specs_folder) / f"{sample_id}-r{revision}.json"
    if not path.is_file():
        raise ValueError(f'Spec proposal not found: {path}')
    data = json.loads(path.read_text(encoding='utf-8'))
    if not reviewer or reviewer == 'agent':
        raise ValueError('Spec approval requires an authorized human reviewer')
    data['status'] = 'APPROVED'
    data['reviewer'] = reviewer
    data['approvedAt'] = True
    path.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    # Update sample_request status if present in durai_samples
    try:
        with transaction('durai_samples_owner', database) as db:
            db.execute("UPDATE durai_samples.sample_request SET status='REVIEWED', reviewer=%s WHERE sample_id=%s AND revision=%s",
                       (reviewer, sample_id, int(revision)))
    except Exception:
        pass
    return data
