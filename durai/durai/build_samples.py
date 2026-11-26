"""Sample: Read design/samples/trading-v1.json. Get S-TRADE-BOOK. Check revision 1 and the SHA-256 of its request text."""
import hashlib
import json
from pathlib import Path

from .pack_data import design_path

DEFAULT_NAME = 'samples/trading-v1.json'
PUBLIC_FIELDS = ('sampleId', 'revision', 'title', 'requestText', 'digest')


class SampleSource:
    def get(self, sample_id):
        # Sample: Get S-TRADE-BOOK. Return sampleId, revision, title, requestText and digest.
        raise NotImplementedError

    def list(self, domain=None):
        # Sample: List domain trading. Return six public records.
        raise NotImplementedError


class JsonSampleSource(SampleSource):
    def __init__(self, path=None, statuses=('REVIEWED',)):
        # Sample: Open trading-v1.json. Hold six REVIEWED samples. Refuse a record whose digest differs from its text. With statuses PROPOSED, serve a PROPOSED record.
        self.statuses = tuple(statuses)
        data = json.loads((Path(path) if path else design_path(DEFAULT_NAME)).read_text(encoding='utf-8'))
        self.domain = data['domain']
        self.records = {}
        for record in data['samples']:
            if hashlib.sha256(record['requestText'].encode('utf-8')).hexdigest() != record['digest']:
                raise ValueError('Sample ' + record['sampleId'] + ' digest differs from its request text')
            self.records[record['sampleId']] = record

    def get(self, sample_id):
        # Sample: Get S-TRADE-CASH. Return the public fields only. Get S-NONE. Raise KeyError.
        record = self.records.get(sample_id)
        if record is None or record['status'] not in self.statuses:
            raise KeyError('Unknown sample ' + str(sample_id))
        return {name: record[name] for name in PUBLIC_FIELDS}

    def list(self, domain=None):
        # Sample: List domain trading. Return six records. List domain health. Return none.
        if domain not in (None, self.domain):
            return []
        return [self.get(sample_id) for sample_id in self.records if self.records[sample_id]['status'] in self.statuses]
