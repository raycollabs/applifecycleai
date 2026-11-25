"""Record dependency licenses and source identities without private configuration."""
import importlib.metadata
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
records = []
for package in sorted(importlib.metadata.distributions(), key=lambda value: value.metadata['Name'].lower()):
    metadata = package.metadata
    license_text = metadata.get('License-Expression') or metadata.get('License') or ''
    classifiers = [value.removeprefix('License :: ') for value in metadata.get_all('Classifier', []) if value.startswith('License ::')]
    records.append({'name': metadata['Name'], 'version': package.version,
                    'licenseExpression': license_text if len(license_text) < 160 else 'See installed distribution license files',
                    'licenseClassifiers': classifiers,
                    'projectUrls': metadata.get_all('Project-URL', [])})
(ROOT / 'receipts/dependencies.json').write_text(json.dumps(records, indent=2) + '\n', encoding='utf-8')
print('Recorded', len(records), 'locked installed distributions.')
