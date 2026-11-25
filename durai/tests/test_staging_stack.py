"""Sample: Render the staging compose model and check its published ports, project guard, and secret files."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import staging_stack as stack

PROJECT = 'durai-stage-0a1b2c3d4e5f'


class StagingStackTests(unittest.TestCase):
    def test_only_proxy_publishes_a_port(self):
        # Sample: Render the compose model. Check one published port 127.0.0.1:8443 on service proxy.
        with tempfile.TemporaryDirectory() as folder:
            model = stack.render_compose(PROJECT,folder)
        published = {name:service['ports'] for name,service in model['services'].items() if service.get('ports')}
        self.assertEqual(published,{'proxy':[{'target':8443,'published':'8443','host_ip':'127.0.0.1','protocol':'tcp'}]})
        self.assertTrue(model['networks']['backend']['internal'])
        self.assertTrue(all(name.startswith(PROJECT+'-') for name in (s['container_name'] for s in model['services'].values())))

    def test_down_refuses_other_projects(self):
        # Sample: Call down with project "prod-reservations". Check ValueError and no docker command.
        commands = []
        for name in ('prod-reservations','durai-stage-0A1B2C3D4E5F','durai-stage-0a1b2c3d4e5','durai-stage-0a1b2c3d4e5f-x'):
            with self.assertRaises(ValueError):
                stack.down(name,run=lambda *args,**kwargs:commands.append(args))
        self.assertEqual(commands,[])

    def test_secrets_not_in_compose(self):
        # Sample: Render with token T-SECRET-1. Check the text has no "T-SECRET-1".
        with tempfile.TemporaryDirectory() as folder:
            values = stack.make_secrets(folder,token='T-SECRET-1')
            text = json.dumps(stack.render_compose(PROJECT,folder))+stack.caddyfile(8443)
            self.assertEqual((Path(folder)/'secrets'/'token').read_text(),'T-SECRET-1')
        hidden = ['T-SECRET-1',values['redis'],values['replication'],*values['passwords'].values()]
        self.assertEqual([item for item in hidden if item in text],[])


if __name__=='__main__':
    unittest.main()
