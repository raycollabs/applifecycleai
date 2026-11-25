"""Portable replica recovery checks with synthetic source and snapshots."""
import json
from pathlib import Path
import tempfile
import unittest
from durai.common import canonical, digest, Conflict
from durai.generation import write_bundle
from durai.replica_restore import restore_replica


class ReplicaRestoreTests(unittest.TestCase):
    def setUp(self):
        # Sample: Seal source S-1 with two lots of 100 and a separate candidate.
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.package = self.root / 'package'
        self.destination = self.root / 'restored'
        source, candidate = b'print(2)\n', b'print(200)\n'
        snapshot = {'lots': 2, 'lot_size': 100}
        replica = {'format': 'durai-system-replica/v1', 'sourceDigest': digest(source),
                   'snapshot': snapshot, 'snapshotDigest': digest(snapshot)}
        self.files = {'baseline/program.py': source, 'candidate/program.py': candidate,
                      'replica.json': canonical(replica),
                      'comparison.json': canonical({'candidateDigest': digest(candidate)})}
        self.manifest = write_bundle(self.package, self.files)

    def test_restore_preserves_bytes(self):
        # Sample: Restore S-1 to a new folder. Check exact files and no execution.
        result = restore_replica(self.package, self.destination, digest(self.manifest))
        for name, data in self.files.items():
            self.assertEqual((self.destination / name).read_bytes(), data)
        self.assertEqual(json.loads((self.destination / 'manifest.json').read_bytes()), self.manifest)
        self.assertFalse(result['executionStarted'])
        self.assertFalse(result['approvalCreated'])

    def test_restore_rejects_tampered_file(self):
        # Sample: Change one baseline byte. Reject before the destination exists.
        (self.package / 'baseline/program.py').write_bytes(b'changed')
        with self.assertRaises(Conflict):
            restore_replica(self.package, self.destination, digest(self.manifest))
        self.assertFalse(self.destination.exists())

    def test_restore_rejects_unsafe_path(self):
        # Sample: Add ../outside.py to the sealed list. Reject and write no outside file.
        self.manifest['files']['../outside.py'] = digest(b'bad')
        (self.package / 'manifest.json').write_bytes(canonical(self.manifest))
        with self.assertRaises(Conflict):
            restore_replica(self.package, self.destination, digest(self.manifest))
        self.assertFalse((self.root / 'outside.py').exists())
        self.assertFalse(self.destination.exists())

    def test_restore_requires_original_manifest_digest(self):
        # Sample: Replace source and update its local hash. Keep the trusted receipt hash.
        original = digest(self.manifest)
        self.manifest['files']['baseline/program.py'] = digest(b'changed')
        (self.package / 'manifest.json').write_bytes(canonical(self.manifest))
        with self.assertRaises(Conflict):
            restore_replica(self.package, self.destination, original)
        self.assertFalse(self.destination.exists())

    def test_restore_preserves_existing_destination(self):
        # Sample: Keep an existing restore folder. Refuse a second publication.
        self.destination.mkdir()
        marker = self.destination / 'keep.txt'
        marker.write_bytes(b'keep')
        with self.assertRaises(FileExistsError):
            restore_replica(self.package, self.destination, digest(self.manifest))
        self.assertEqual(marker.read_bytes(), b'keep')
