"""Sample: Test validation and digest stability of unified declarative app manifests."""
import pytest
from durai.common import Conflict
from durai.app_manifest import validate, digest, FORMAT


class TestAppManifest:
    def sample_manifest(self):
        # Sample: Return valid sample app manifest dictionary.
        return {
            'format': FORMAT,
            'id': 'trade-pilot',
            'version': '1.0',
            'routes': {
                'builder': 'deepseek-flash',
                'c_rev': 'deepseek-chat',
                'p_rev': 'gemini-1.5-flash',
                's_rev': 'human',
            },
            'caps': {
                'callsPerComponent': 100,
                'totalCalls': 500,
                'costEnterpriseUSD': 25.0,
                'stallNudge': 30,
                'stallStop': 50,
            },
            'reviewers': ['C-REV', 'P-REV', 'S-REV'],
            'components': ['S-TRADE-ACCESS', 'S-TRADE-BOOK'],
            'workflow': {
                'format': 'durai-workflow/v1',
                'id': 'trade-pilot-wf',
                'steps': [
                    {'id': 'step-1', 'goal': 'Implement access module', 'strategy': 'SINGLE'},
                ],
            },
        }

    def test_valid_manifest_passes_validation(self):
        # Sample: Validate correct sample manifest and check parsed values.
        m = self.sample_manifest()
        norm = validate(m)
        assert norm['id'] == 'trade-pilot'
        assert norm['caps']['callsPerComponent'] == 100
        assert norm['reviewers'] == ['C-REV', 'P-REV', 'S-REV']

    def test_manifest_digest_is_deterministic(self):
        # Sample: Digest valid manifest twice. Verify digests match.
        m = self.sample_manifest()
        d1 = digest(m)
        d2 = digest(m)
        assert d1 == d2
        assert len(d1) == 64

    def test_missing_builder_route_raises_conflict(self):
        # Sample: Omit builder from routes. Verify Conflict is raised.
        m = self.sample_manifest()
        m['routes'] = {'c_rev': 'deepseek-chat'}
        with pytest.raises(Conflict, match='must specify a builder role'):
            validate(m)

    def test_unknown_reviewer_raises_conflict(self):
        # Sample: Provide invalid reviewer role. Verify Conflict is raised.
        m = self.sample_manifest()
        m['reviewers'] = ['UNKNOWN-REV']
        with pytest.raises(Conflict, match='Unknown reviewer role'):
            validate(m)
