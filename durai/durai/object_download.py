"""Sample: Authorize subject A for O-1. Request a signed GET for 60 seconds."""
from urllib.parse import urlsplit

from .db import transaction
from .outbox import check_name


def download(subject, object_id, signer, *, ttl_seconds=60, database='durai_test', role='durai_runtime'):
    # Sample: Read the object grant for subject A. Call the signer only when the grant exists.
    check_name(subject)
    check_name(object_id)
    if not callable(signer) or type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 300:
        raise ValueError('Use a trusted signer and a duration from 1 to 300 seconds')
    with transaction(role,database) as db:
        row = db.execute('SELECT storage_key FROM durai_transactions.object_grant '
                         'WHERE application_id=durai_core.application_id() AND subject=%s AND object_id=%s',
                         (subject,object_id)).fetchone()
    if row is None:
        raise KeyError('Object unavailable to this subject')
    link = signer(row['storage_key'], ttl_seconds)
    if not isinstance(link,str):
        raise ValueError('Signer did not return a link')
    parts = urlsplit(link)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.fragment:
        raise ValueError('Signer did not return an HTTPS GET link')
    return {'url':link,'expiresInSeconds':ttl_seconds}
