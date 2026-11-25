"""Sample: Embed a seat hold and a booking question. Return 1024 numbers for each input."""
import json
import math
import urllib.error
import urllib.request
from .common import Conflict

MODEL = 'nvidia/nv-embedqa-e5-v5'
DIMENSIONS = 1024


def vector_text(value):
    # Sample: Encode 1024 finite numbers. Refuse a zero vector or a missing dimension.
    if (not isinstance(value, list) or len(value) != DIMENSIONS or
            any(isinstance(n, bool) or not isinstance(n, (float, int)) or not math.isfinite(n) for n in value) or
            not any(n != 0 for n in value)):
        raise Conflict('Embedding must contain 1024 finite numbers and have nonzero length')
    return '[' + ','.join(str(float(n)) for n in value) + ']'


class NvidiaEmbeddings:
    model = MODEL

    def embed(self, texts, mode):
        # Sample: Embed 2 passages with the configured NVIDIA key. Return 2 ordered vectors.
        from .models import configuration
        from .model_routes import providers
        if mode not in ('passage', 'query') or not 1 <= len(texts) <= 32 or any(not isinstance(t, str) or not t.strip() for t in texts):
            raise ValueError('Embedding input is outside the public contract')
        profile = next((p for p in providers(configuration()) if p['id'] == 'nvidia' and p['enabled'] and p['apiKey']), None)
        if not profile or profile['baseUrl'].rstrip('/') != 'https://integrate.api.nvidia.com/v1':
            raise Conflict('Configure the NVIDIA embedding provider explicitly')
        payload = {'model': self.model, 'input': texts, 'input_type': mode, 'truncate': 'NONE', 'encoding_format': 'float'}
        request = urllib.request.Request(profile['baseUrl'].rstrip('/') + '/embeddings',
            data=json.dumps(payload).encode(), headers={'Authorization': 'Bearer ' + profile['apiKey'], 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                result = json.load(response)
        except (urllib.error.URLError, TimeoutError, ValueError):
            raise Conflict('NVIDIA embedding request failed; no semantic fallback was used') from None
        rows = sorted(result.get('data', []), key=lambda row: row.get('index', -1))
        if [row.get('index') for row in rows] != list(range(len(texts))):
            raise Conflict('Embedding response count or order differs')
        vectors = [row.get('embedding') for row in rows]
        for vector in vectors:
            vector_text(vector)
        return vectors
