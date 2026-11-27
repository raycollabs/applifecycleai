import hashlib
import os
from urllib.parse import urlsplit

from flask_appbuilder.security.manager import AUTH_OAUTH
from superset.security import SupersetSecurityManager


PROVIDER = 'keycloak'
ROLE_GROUPS = {
    'durai-oil-readers': ['oil-reader'],
    'durai-gas-readers': ['gas-reader'],
    'durai-oil-authors': ['oil-author'],
    'durai-gas-authors': ['gas-author'],
    'durai-oil-analysts': ['oil-reader', 'oil-analyst'],
}


class ReportingSecurityManager(SupersetSecurityManager):
    def get_oauth_user_info(self, provider, response):
        # Sample: Return durai-oil-readers from Keycloak. Give the session the oil-reader role.
        if provider != PROVIDER:
            return super().get_oauth_user_info(provider, response)
        userinfo = self.oauth_remotes[provider].get('userinfo', timeout=10)
        userinfo.raise_for_status()
        profile = userinfo.json()
        groups = profile.get('groups', [])
        if not isinstance(groups, list) or any(not isinstance(group, str) for group in groups):
            raise ValueError('Use a list of group names')
        return {'username': 'oidc-' + profile['sub'], 'email': profile['email'],
                'first_name': profile.get('given_name', ''), 'last_name': profile.get('family_name', ''),
                'role_keys': [group for group in groups if group in ROLE_GROUPS]}


def settings():
    # Sample: Set the local test issuer. Check auth code login with PKCE.
    issuer = os.environ['DURAI_REPORTING_OIDC_ISSUER'].rstrip('/')
    url = urlsplit(issuer)
    is_test = (url.scheme == 'http' and url.netloc == 'oidc.durai.test:58080' and
               os.environ.get('DURAI_REPORTING_OIDC_DEV') == '1')
    if not url.hostname or url.username or url.query or url.fragment or (url.scheme != 'https' and not is_test):
        raise ValueError('Use an HTTPS OpenID Connect issuer')
    client_id = os.environ['DURAI_REPORTING_OIDC_CLIENT_ID']
    cookie_suffix = hashlib.sha256(client_id.encode()).hexdigest()[:12]
    return {
        'AUTH_TYPE': AUTH_OAUTH,
        'SESSION_COOKIE_NAME': 'superset_session_' + cookie_suffix,
        'SESSION_COOKIE_SECURE': url.scheme == 'https',
        'SESSION_COOKIE_SAMESITE': 'Lax',
        'CUSTOM_SECURITY_MANAGER': ReportingSecurityManager,
        'AUTH_USER_REGISTRATION': True,
        'AUTH_USER_REGISTRATION_ROLE': 'unscoped',
        'AUTH_ROLES_SYNC_AT_LOGIN': True,
        'AUTH_ROLES_MAPPING': ROLE_GROUPS,
        'OAUTH_PROVIDERS': [{'name': PROVIDER, 'icon': 'fa-key', 'token_key': 'access_token',
                             'remote_app': {'client_id': client_id,
                                            'client_secret': os.environ['DURAI_REPORTING_OIDC_CLIENT_SECRET'],
                                            'server_metadata_url': issuer + '/.well-known/openid-configuration',
                                            'api_base_url': issuer + '/protocol/openid-connect/',
                                            'client_kwargs': {'scope': 'openid email profile',
                                                              'code_challenge_method': 'S256',
                                                              'default_timeout': 10}}}],
    }
