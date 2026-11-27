import argparse
import json
from pathlib import Path
from secrets import token_urlsafe


GROUPS = {'oil': ['durai-oil-readers'], 'gas': ['durai-gas-readers'],
          'analyst': ['durai-oil-analysts'], 'unscoped': []}
PORTS = {'pg': 55482, 'ch': 58061}


def main():
    # Sample: Create the test realm. Check that sso-gas belongs only to durai-gas-readers.
    parser = argparse.ArgumentParser(description='Create the local reporting identity fixture')
    parser.add_argument('directory', type=Path)
    target = parser.parse_args().directory
    target.mkdir(parents=True, exist_ok=True)
    mapper = {'name': 'desk groups', 'protocol': 'openid-connect', 'protocolMapper': 'oidc-group-membership-mapper',
              'config': {'claim.name': 'groups', 'full.path': 'false', 'userinfo.token.claim': 'true'}}
    clients = [{'clientId': f'durai-reporting-{key}', 'secret': token_urlsafe(32), 'enabled': True,
                'protocol': 'openid-connect', 'standardFlowEnabled': True, 'directAccessGrantsEnabled': False,
                'redirectUris': [f'http://127.0.0.1:{port}/oauth-authorized/keycloak'],
                'defaultClientScopes': ['profile', 'email'], 'protocolMappers': [mapper],
                'attributes': {'pkce.code.challenge.method': 'S256'}} for key, port in PORTS.items()]
    users = [{'username': f'sso-{key}', 'email': f'sso-{key}@example.invalid', 'firstName': 'Test',
              'lastName': key, 'enabled': True, 'emailVerified': True, 'groups': groups, 'requiredActions': [],
              'credentials': [{'type': 'password', 'value': token_urlsafe(24), 'temporary': False}]}
             for key, groups in GROUPS.items()]
    groups = sorted({group for names in GROUPS.values() for group in names})
    realm = {'realm': 'durai-reporting', 'enabled': True, 'sslRequired': 'none',
             'groups': [{'name': name} for name in groups], 'users': users, 'clients': clients}
    document = target / 'durai-reporting-realm.json'
    with document.open('x', encoding='utf-8') as file:
        json.dump(realm, file)


if __name__ == '__main__':
    main()
