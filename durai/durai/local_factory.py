"""Operator commands for the five-phase local software factory."""
import json
from pathlib import Path
from .common import digest
from .db import transaction
from .packs import PackStore, generic_pack
from .generation import generate, DEFAULT_SPEC
from .replica import evolve
from .archives import ensure_archive
from .common import canonical

ROOT = Path(__file__).resolve().parents[1]


def save_receipt(name, value):
    # Sample: Save the greenfield run ID and digests. Keep model keys out of receipts.
    path = ROOT / 'receipts' / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, default=str) + '\n', encoding='utf-8')


def install_ontology(database='durai_local', capture_live=False, with_trading=False):
    # Sample: Install G-1 and optional T-1. Authorize only isolated synthetic bindings.
    from .trading_import import import_trading
    store = PackStore(database)
    generic = store.install(generic_pack())
    generic_projection = store.project(generic)
    generic_binding = store.bind('greenfield-generic', [generic], 'SYNTHETIC_LOCAL')
    imported, domain, trading_binding = None, None, None
    if with_trading or capture_live:
        imported = import_trading(store, capture_live)
        domain = imported['reference']
        trading_binding = store.bind('exchange-synthetic', [generic, domain], 'SYNTHETIC_LOCAL')
        store.bind('exchange-reference', [generic, domain], 'REFERENCE_ONLY')
    archive = ROOT / 'private/ontology-imports' / ('generic-' + generic['digest'] + '.sqlite')
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive_digest = ensure_archive(archive, {'ontology/pack.json': canonical(store.get(generic))}, {'pack': generic})
    value = {'state': 'READY', 'generic': generic, 'genericProjection': generic_projection,
             'application': domain, 'tradingImport': imported,
             'bindings': {'genericOnly': generic_binding['digest'], 'exchange': trading_binding['digest'] if trading_binding else None},
             'defaultInstallation': 'generic pack; trading pack is optional',
             'genericArchive': str(archive), 'genericArchiveDigest': archive_digest,
             'releaseAuthority': False}
    save_receipt('ontology-install.json' if domain else 'ontology-generic-install.json', value)
    return value


def ontology_status(database='durai_local'):
    # Sample: List installed packs and workflow states. Change no records.
    store = PackStore(database)
    with transaction(database=database) as db:
        bindings = db.execute('SELECT id,revision,digest,document FROM durai_ontology.binding ORDER BY id,revision').fetchall()
        runs = db.execute('SELECT r.id,r.mode,r.binding_digest,r.context_digest,COALESCE((SELECT stage FROM durai_engineering.evidence e WHERE e.run_id=r.id AND e.application_id=r.application_id ORDER BY sequence DESC LIMIT 1),\'DRAFT\') AS state FROM durai_engineering.run r ORDER BY r.created_at DESC LIMIT 20').fetchall()
    return {'packs': store.discover(), 'bindings': bindings, 'runs': runs, 'readOnly': True}


def preserve_target(database='durai_local'):
    # Sample: Capture the active trading version and approved position before new proofs.
    with transaction(database=database) as db:
        return {'targets': db.execute('SELECT id,active_version,generation,state FROM durai_governance.target ORDER BY id').fetchall(),
                'trades': db.execute('SELECT * FROM app_trading.trade ORDER BY id').fetchall(),
                'approvals': db.execute('SELECT id,version_id,digest,target,authority FROM durai_governance.approval ORDER BY id').fetchall()}


def five_phase(database='durai_local', use_model=False):
    # Sample: Install both ontology kinds, build a new app, and repair an isolated replica.
    before = preserve_target(database)
    ontology = install_ontology(database, capture_live=True, with_trading=True)
    generic = generate('greenfield-generic', dict(DEFAULT_SPEC), database)
    greenfield = generate('exchange-synthetic', dict(DEFAULT_SPEC), database, use_model=use_model)
    replica = evolve('exchange-synthetic', database)
    after = preserve_target(database)
    if before != after:
        raise ValueError('The original approved target changed during qualification')
    value = {'state': 'PASS', 'scope': 'Five-phase local synthetic vertical slice',
             'ontology': ontology, 'genericOnlyGreenfield': generic, 'applicationGreenfield': greenfield,
             'replica': replica, 'existingTargetUnchanged': True, 'originalStateDigest': digest(before),
             'newReleaseApprovals': 0, 'productionOrBillionTokenScaleQualified': False}
    save_receipt('five-phase.json', value)
    return value
