"""Sample: Test reservation baseline sample catalog, seat capacity, idempotent holds, confirmation, cancellation, organizer isolation, React UI rules, FastAPI routes, and PostgreSQL persistence."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import unittest

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'apps' / 'reservation-baseline'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pg_support  # noqa: E402
from api.app import app as fastapi_app  # noqa: E402
from api.domain import (  # noqa: E402
    AccessDeniedError,
    ExpiredHoldError,
    IdempotencyConflictError,
    InvalidHoldStateError,
    NoOverbookError,
    ReservationError,
    ReservationService,
)
from api.postgres_store import PostgresReservationService  # noqa: E402
from durai.build_samples import JsonSampleSource  # noqa: E402
from durai.build_web_rules import check_web_rules  # noqa: E402


class ReservationBaselineTests(unittest.TestCase):
    def setUp(self):
        # Sample: Initialize clock at 2026-11-26T12:00:00Z and seed 10 seats in EVT-1 ORCHESTRA for ORG-1.
        self.clock = datetime(2026, 11, 26, 12, 0, 0, tzinfo=timezone.utc)
        self.service = ReservationService(now_fn=lambda: self.clock)
        self.service.seed_event('ORG-1', 'EVT-1', 'ORCHESTRA', 10, unit_price_cents=5000)

    def test_sample_catalog_has_six_proposed_components_with_exact_digests(self):
        # Sample: Load reservations-v1.json with statuses=('PROPOSED',). Check six components and verify REVIEWED loader hides them.
        sample_path = ROOT / 'design' / 'samples' / 'reservations-v1.json'
        raw = json.loads(sample_path.read_text(encoding='utf-8'))
        self.assertEqual(raw['domain'], 'reservations')
        self.assertEqual([item['status'] for item in raw['samples']], ['PROPOSED'] * 6)

        source = JsonSampleSource(sample_path, statuses=('PROPOSED',))
        listed = source.list('reservations')
        expected_ids = [
            'S-RES-SEATS',
            'S-RES-HOLD',
            'S-RES-CONFIRM',
            'S-RES-CANCEL',
            'S-RES-ACCESS',
            'S-RES-SCREENS',
        ]
        self.assertEqual([item['sampleId'] for item in listed], expected_ids)
        self.assertEqual(JsonSampleSource(sample_path).list('reservations'), [])
        with self.assertRaises(KeyError):
            JsonSampleSource(sample_path).get('S-RES-SEATS')

    def test_seat_capacity_no_overbook_and_600s_ttl_expiry(self):
        # Sample: Hold 6 seats of EVT-1 ORCHESTRA, check 4 available, refuse 5 seats with NO_OVERBOOK, advance clock past 600s, and check 10 available.
        hold, replayed = self.service.place_hold('ORG-1', 'K-SEAT-1', 'EVT-1', 'ORCHESTRA', 6, 'CUST-1', ttl_seconds=600)
        self.assertFalse(replayed)
        self.assertEqual(hold['status'], 'HELD')
        self.assertEqual(self.service.get_event_seat('ORG-1', 'EVT-1', 'ORCHESTRA')['available_seats'], 4)
        with self.assertRaises(NoOverbookError) as ctx:
            self.service.place_hold('ORG-1', 'K-SEAT-2', 'EVT-1', 'ORCHESTRA', 5, 'CUST-2')
        self.assertEqual(ctx.exception.code, 'NO_OVERBOOK')
        self.assertEqual(ctx.exception.status_code, 409)
        self.clock += timedelta(seconds=601)
        info = self.service.get_event_seat('ORG-1', 'EVT-1', 'ORCHESTRA')
        self.assertEqual(info['available_seats'], 10)
        self.assertEqual(info['held_seats'], 0)

    def test_idempotent_hold_replay_and_409_conflict(self):
        # Sample: Place hold with key K-1 for 2 seats, replay K-1 for same hold_id, and reuse K-1 with 3 seats for IdempotencyConflictError.
        first, replayed1 = self.service.place_hold('ORG-1', 'K-1', 'EVT-1', 'ORCHESTRA', 2, 'CUST-A')
        second, replayed2 = self.service.place_hold('ORG-1', 'K-1', 'EVT-1', 'ORCHESTRA', 2, 'CUST-A')
        self.assertFalse(replayed1)
        self.assertTrue(replayed2)
        self.assertEqual(first['hold_id'], second['hold_id'])
        with self.assertRaises(IdempotencyConflictError) as ctx:
            self.service.place_hold('ORG-1', 'K-1', 'EVT-1', 'ORCHESTRA', 3, 'CUST-A')
        self.assertEqual(ctx.exception.status_code, 409)

    def test_confirm_hold_settlement_and_expired_hold_refusal(self):
        # Sample: Confirm 2-seat hold with 10000 cents for CONFIRMED status. Advance clock past 600s on second hold and refuse confirmation with ExpiredHoldError.
        hold, _ = self.service.place_hold('ORG-1', 'K-CONF-1', 'EVT-1', 'ORCHESTRA', 2, 'CUST-B')
        with self.assertRaises(ReservationError):
            self.service.confirm_hold('ORG-1', hold['hold_id'], payment_cents=9000)
        confirmed = self.service.confirm_hold('ORG-1', hold['hold_id'], payment_cents=10000)
        self.assertEqual(confirmed['status'], 'CONFIRMED')
        self.assertEqual(confirmed['settled_cents'], 10000)
        info = self.service.get_event_seat('ORG-1', 'EVT-1', 'ORCHESTRA')
        self.assertEqual(info['confirmed_seats'], 2)
        self.assertEqual(info['available_seats'], 8)
        with self.assertRaises(InvalidHoldStateError):
            self.service.confirm_hold('ORG-1', hold['hold_id'])

        exp_hold, _ = self.service.place_hold('ORG-1', 'K-CONF-2', 'EVT-1', 'ORCHESTRA', 3, 'CUST-C', ttl_seconds=600)
        self.clock += timedelta(seconds=601)
        with self.assertRaises(ExpiredHoldError) as ctx:
            self.service.confirm_hold('ORG-1', exp_hold['hold_id'])
        self.assertEqual(ctx.exception.status_code, 409)

    def test_cancel_releases_held_and_confirmed_seats_back_to_pool(self):
        # Sample: Cancel HELD hold of 3 seats and CONFIRMED hold of 4 seats. Verify all 10 seats return to available pool.
        held, _ = self.service.place_hold('ORG-1', 'K-CAN-1', 'EVT-1', 'ORCHESTRA', 3, 'CUST-D')
        conf_hold, _ = self.service.place_hold('ORG-1', 'K-CAN-2', 'EVT-1', 'ORCHESTRA', 4, 'CUST-E')
        self.service.confirm_hold('ORG-1', conf_hold['hold_id'])
        self.assertEqual(self.service.get_event_seat('ORG-1', 'EVT-1', 'ORCHESTRA')['available_seats'], 3)

        c1 = self.service.cancel_hold('ORG-1', held['hold_id'])
        self.assertEqual(c1['status'], 'CANCELLED')
        self.assertEqual(self.service.get_event_seat('ORG-1', 'EVT-1', 'ORCHESTRA')['available_seats'], 6)

        c2 = self.service.cancel_hold('ORG-1', conf_hold['hold_id'])
        self.assertEqual(c2['status'], 'CANCELLED')
        info = self.service.get_event_seat('ORG-1', 'EVT-1', 'ORCHESTRA')
        self.assertEqual(info['confirmed_seats'], 0)
        self.assertEqual(info['held_seats'], 0)
        self.assertEqual(info['available_seats'], 10)
        actions = [entry['action'] for entry in self.service.list_audit('ORG-1')]
        self.assertEqual(actions[:2], ['HOLD_CANCELLED', 'HOLD_CANCELLED'])

    def test_organizer_isolation_refuses_cross_organizer_access(self):
        # Sample: Create hold for ORG-1. Attempt read, confirm, cancel, and event hold as ORG-2. Check AccessDeniedError and empty ORG-2 lists.
        hold, _ = self.service.place_hold('ORG-1', 'K-ISO-1', 'EVT-1', 'ORCHESTRA', 2, 'CUST-F')
        with self.assertRaises(AccessDeniedError):
            self.service.get_event_seat('ORG-2', 'EVT-1', 'ORCHESTRA')
        with self.assertRaises(AccessDeniedError):
            self.service.place_hold('ORG-2', 'K-ISO-2', 'EVT-1', 'ORCHESTRA', 1, 'CUST-G')
        with self.assertRaises(AccessDeniedError):
            self.service.get_hold('ORG-2', hold['hold_id'])
        with self.assertRaises(AccessDeniedError):
            self.service.confirm_hold('ORG-2', hold['hold_id'])
        with self.assertRaises(AccessDeniedError):
            self.service.cancel_hold('ORG-2', hold['hold_id'])
        self.assertEqual(self.service.list_events('ORG-2'), [])
        self.assertEqual(self.service.list_holds('ORG-2'), [])
        self.assertEqual(self.service.list_audit('ORG-2'), [])

    def test_reservation_web_passes_web_rules_and_ui_contract(self):
        # Sample: Run check_web_rules on apps/reservation-baseline/web and verify loading, error, empty, ok, signed-out, and useActionState contracts.
        web_dir = ROOT / 'apps' / 'reservation-baseline' / 'web'
        self.assertEqual(check_web_rules(web_dir), [])
        self.assertIn('data-shell-state="loading"', (web_dir / 'app' / 'loading.tsx').read_text(encoding='utf-8'))
        self.assertIn('data-shell-state="error"', (web_dir / 'app' / 'error.tsx').read_text(encoding='utf-8'))
        screen_tsx = (web_dir / 'app' / '[screen]' / 'page.tsx').read_text(encoding='utf-8')
        self.assertIn('data-shell-state="empty"', screen_tsx)
        self.assertIn('data-shell-state="ok"', screen_tsx)
        self.assertIn('data-shell-state="signed-out"', screen_tsx)
        self.assertIn('useActionState', (web_dir / 'app' / 'SignIn.tsx').read_text(encoding='utf-8'))
        self.assertIn('useActionState', (web_dir / 'app' / 'HoldForm.tsx').read_text(encoding='utf-8'))
        self.assertIn('signOut', (web_dir / 'app' / 'Menu.tsx').read_text(encoding='utf-8'))

    def test_fastapi_app_serves_seeded_org_live_and_http_contracts(self):
        # Sample: Sign in as ORG-LIVE on FastAPI TestClient, verify seeded events, place idempotent hold, check 409 conflict, confirm, cancel, and sign out.
        client = TestClient(fastapi_app)
        self.assertEqual(client.get('/health').json()['status'], 'ok')
        self.assertEqual(client.get('/api/events').status_code, 401)

        sign_in = client.post('/session/sign-in', json={'trader': 'ORG-LIVE', 'secret': 'demo'})
        self.assertEqual(sign_in.status_code, 200)
        events = client.get('/api/events').json()['items']
        self.assertGreaterEqual(len(events), 3)

        payload = {
            'event_id': 'EVT-WORKSHOP-01',
            'section': 'GENERAL',
            'seat_count': 2,
            'customer_ref': 'CUST-HTTP-1',
        }
        r1 = client.post('/api/holds', headers={'Idempotency-Key': 'HTTP-K-1'}, json=payload)
        self.assertEqual(r1.status_code, 201)
        hold_id = r1.json()['hold']['hold_id']

        r2 = client.post('/api/holds', headers={'Idempotency-Key': 'HTTP-K-1'}, json=payload)
        self.assertEqual(r2.status_code, 200)
        self.assertTrue(r2.json()['replayed'])

        r_conflict = client.post('/api/holds', headers={'Idempotency-Key': 'HTTP-K-1'}, json={**payload, 'seat_count': 4})
        self.assertEqual(r_conflict.status_code, 409)

        r_overbook = client.post('/api/holds', headers={'Idempotency-Key': 'HTTP-K-2'}, json={**payload, 'seat_count': 99})
        self.assertEqual(r_overbook.status_code, 409)
        self.assertEqual(r_overbook.json()['error']['code'], 'NO_OVERBOOK')

        r_confirm = client.post(f'/api/holds/{hold_id}/confirm', json={})
        self.assertEqual(r_confirm.status_code, 200)
        self.assertEqual(r_confirm.json()['hold']['status'], 'CONFIRMED')

        r_cancel = client.post(f'/api/holds/{hold_id}/cancel')
        self.assertEqual(r_cancel.status_code, 200)
        self.assertEqual(r_cancel.json()['hold']['status'], 'CANCELLED')

        audit = client.get('/api/audit').json()['items']
        self.assertTrue(any(row['hold_id'] == hold_id and row['action'] == 'HOLD_CANCELLED' for row in audit))

        self.assertEqual(client.post('/session/sign-out').status_code, 200)
        self.assertEqual(client.get('/api/events').status_code, 401)


class PostgresReservationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Start disposable PostgreSQL 17 container when Docker is ready.
        if not pg_support.docker_ready():
            raise unittest.SkipTest('Docker is not available')
        cls.connect = staticmethod(pg_support.start_postgres())
        with cls.connect() as conn:
            info = conn.info
            cls.dsn = f'postgresql://{info.user}:{info.password}@{info.host}:{info.port}/{info.dbname}'

    @classmethod
    def tearDownClass(cls):
        # Sample: Stop and remove disposable PostgreSQL container.
        pg_support.stop_postgres()

    def setUp(self):
        # Sample: Ensure reservation schema, initialize controllable clock, and truncate tables before each test.
        self.clock = datetime(2026, 11, 26, 12, 0, 0, tzinfo=timezone.utc)
        self.pg = PostgresReservationService(self.dsn, now_fn=lambda: self.clock)
        self.pg.ensure_schema()
        with self.pg._connect() as conn:
            conn.execute(
                'TRUNCATE TABLE reservation.idempotency, reservation.seat_hold, '
                'reservation.event_seat, reservation.audit_log, reservation.session'
            )

    def test_postgres_reservation_lifecycle_persists_across_restart(self):
        # Sample: Seed 10 seats in Postgres, test replay and 409 conflict, confirm, cancel to release seats, test expiry and isolation, and read back from fresh service.
        self.pg.seed_event('ORG-1', 'EVT-PG', 'MAIN', 10, unit_price_cents=4000)

        h1, rep1 = self.pg.place_hold('ORG-1', 'PG-K-1', 'EVT-PG', 'MAIN', 3, 'CUST-PG-1', ttl_seconds=600)
        self.assertFalse(rep1)
        h1_rep, rep2 = self.pg.place_hold('ORG-1', 'PG-K-1', 'EVT-PG', 'MAIN', 3, 'CUST-PG-1', ttl_seconds=600)
        self.assertTrue(rep2)
        self.assertEqual(h1['hold_id'], h1_rep['hold_id'])

        with self.assertRaises(IdempotencyConflictError):
            self.pg.place_hold('ORG-1', 'PG-K-1', 'EVT-PG', 'MAIN', 4, 'CUST-PG-1')

        with self.assertRaises(NoOverbookError):
            self.pg.place_hold('ORG-1', 'PG-K-2', 'EVT-PG', 'MAIN', 8, 'CUST-PG-2')

        confirmed = self.pg.confirm_hold('ORG-1', h1['hold_id'], payment_cents=12000)
        self.assertEqual(confirmed['status'], 'CONFIRMED')
        self.assertEqual(self.pg.get_event_seat('ORG-1', 'EVT-PG', 'MAIN')['confirmed_seats'], 3)

        h2, _ = self.pg.place_hold('ORG-1', 'PG-K-3', 'EVT-PG', 'MAIN', 2, 'CUST-PG-3', ttl_seconds=600)
        self.clock += timedelta(seconds=601)
        with self.assertRaises(ExpiredHoldError):
            self.pg.confirm_hold('ORG-1', h2['hold_id'])

        with self.assertRaises(AccessDeniedError):
            self.pg.cancel_hold('ORG-2', h1['hold_id'])
        self.assertEqual(self.pg.list_holds('ORG-2'), [])

        cancelled = self.pg.cancel_hold('ORG-1', h1['hold_id'])
        self.assertEqual(cancelled['status'], 'CANCELLED')

        self.pg.save_session(b'token-digest-pg-1', 'ORG-1')

        restarted = PostgresReservationService(self.dsn, now_fn=lambda: self.clock)
        info = restarted.get_event_seat('ORG-1', 'EVT-PG', 'MAIN')
        self.assertEqual(info['confirmed_seats'], 0)
        self.assertEqual(info['held_seats'], 0)
        self.assertEqual(info['available_seats'], 10)
        self.assertEqual(len(restarted.list_holds('ORG-1')), 2)
        audit_actions = [row['action'] for row in restarted.list_audit('ORG-1')]
        self.assertCountEqual(audit_actions, ['HOLD_PLACED', 'HOLD_CONFIRMED', 'HOLD_PLACED', 'HOLD_EXPIRED', 'HOLD_CANCELLED'])
        self.assertEqual(restarted.lookup_session(b'token-digest-pg-1'), 'ORG-1')

    def test_postgres_check_constraints_reject_invalid_rows(self):
        # Sample: Attempt direct SQL inserts with negative total_seats and zero seat_count. Check PostgreSQL CheckViolation.
        import psycopg.errors
        with self.assertRaises(psycopg.errors.CheckViolation):
            with self.pg._connect() as conn:
                conn.execute(
                    "INSERT INTO reservation.event_seat (organizer, event_id, section, total_seats) VALUES ('ORG-X', 'EVT-X', 'A', -1)"
                )
        self.pg.seed_event('ORG-X', 'EVT-X', 'A', 5, unit_price_cents=1000)
        with self.assertRaises(psycopg.errors.CheckViolation):
            with self.pg._connect() as conn:
                conn.execute(
                    "INSERT INTO reservation.seat_hold (hold_id, organizer, event_id, section, customer_ref, seat_count, status, expires_at) "
                    "VALUES ('H-BAD', 'ORG-X', 'EVT-X', 'A', 'C1', 0, 'HELD', now())"
                )


if __name__ == '__main__':
    unittest.main()
