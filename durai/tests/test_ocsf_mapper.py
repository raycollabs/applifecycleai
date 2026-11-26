"""Sample: Run OcsfTests. Check that audit and event rows map to OCSF and that OpenShell JSONL is kept as a digest reference."""
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from durai.common import digest  # noqa: E402
from durai.ocsf_mapper import from_audit_row, from_event_row, millis, reference_from_jsonl, to_jsonl  # noqa: E402

TIME = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)
AUDIT = {'id': 7, 'actor': 'durai_reviewer', 'action': 'APPROVE', 'payload': {'subjectDigest': 'abc'}, 'created_at': TIME}
EVENT = {'id': 'E-1', 'run_id': 'W-1', 'sequence': 3, 'kind': 'GATE_OPENED', 'payload': {'stepId': 'egress'}, 'created_at': TIME}


class OcsfTests(unittest.TestCase):
    def test_audit_row_maps_to_ocsf(self):
        # Sample: Audit row 7 by durai_reviewer with action APPROVE gives class 6003, uid audit:7, actor durai_reviewer, operation APPROVE and the payload under unmapped.
        mapped = from_audit_row(AUDIT)
        self.assertEqual((6003, 'audit:7', 'durai_reviewer', 'APPROVE'),
                         (mapped['class_uid'], mapped['metadata']['uid'], mapped['actor']['user']['name'], mapped['api']['operation']))
        self.assertEqual({'subjectDigest': 'abc'}, mapped['unmapped'])
        self.assertEqual(600399, mapped['type_uid'])

    def test_time_is_in_milliseconds(self):
        # Sample: 2026-11-26T12:00:00Z gives 1795694400000. An int stays the same.
        self.assertEqual(1795694400000, millis(TIME))
        self.assertEqual(5, millis(5))
        self.assertEqual(1795694400000, from_audit_row(AUDIT)['time'])

    def test_event_row_keeps_run_and_sequence(self):
        # Sample: Event E-1 of run W-1 sequence 3 gives uid event:E-1 and unmapped runId W-1, sequence 3.
        mapped = from_event_row(EVENT)
        self.assertEqual('event:E-1', mapped['metadata']['uid'])
        self.assertEqual(('W-1', 3, 'egress'), (mapped['unmapped']['runId'], mapped['unmapped']['sequence'], mapped['unmapped']['stepId']))
        self.assertEqual({'stepId': 'egress'}, EVENT['payload'])

    def test_jsonl_has_one_line_per_event(self):
        # Sample: Two events give two lines. Each line parses as JSON. The text ends with a newline.
        text = to_jsonl([from_audit_row(AUDIT), from_event_row(EVENT)])
        self.assertEqual(2, len(text.splitlines()))
        self.assertTrue(text.endswith('\n'))
        self.assertEqual(['audit:7', 'event:E-1'], [json.loads(line)['metadata']['uid'] for line in text.splitlines()])

    def test_openshell_jsonl_stored_as_reference(self):
        # Sample: Three lines with one denied and one unparsed line. The reference has the digest, 4 events, 1 denied and no log body.
        data = (b'{"class_uid": 4001, "action": "Allowed"}\n{"class_uid": 4001, "action": "Denied", "disposition_id": 2}\n'
                b'{"class_uid": 6003, "status": "ok"}\nnot json\n')
        reference = reference_from_jsonl(data, 'sbx-1')
        self.assertEqual((4, 1, digest(data)), (reference['events'], reference['denied'], reference['digest']))
        self.assertEqual({'4001': 2, '6003': 1, 'unparsed': 1}, reference['classes'])
        self.assertNotIn('Allowed', json.dumps(reference))

    def test_empty_log_gives_zero_events(self):
        # Sample: Empty bytes give 0 events, 0 denied and the digest of empty bytes.
        reference = reference_from_jsonl(b'', 'sbx-1')
        self.assertEqual((0, 0), (reference['events'], reference['denied']))


if __name__ == '__main__':
    unittest.main()
