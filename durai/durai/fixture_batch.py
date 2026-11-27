from dataclasses import dataclass
from hashlib import sha256
import json


@dataclass(frozen=True)
class FixtureBatch:
    application: str
    schema: str
    data: dict

    def load(self, application, schema, adapter):
        # Sample: Send a seat batch to a trade adapter. Refuse the batch before a write.
        if (self.application, self.schema) != (application, schema):
            raise ValueError('Fixture application or schema differs from the test target')
        payload = json.dumps(self.data, sort_keys=True, separators=(',', ':'), allow_nan=False)
        digest = sha256(payload.encode('utf-8')).hexdigest()
        return {
            'application': application, 'schema': schema, 'fixture_sha256': digest,
            'load': adapter(json.loads(payload), digest),
        }
