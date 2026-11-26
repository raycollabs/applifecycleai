"""Sample: Run the scripted escape suite on one SandboxPort adapter. Each probe is a small Python program. The receipt lists pass or fail for every probe."""
import json

from .common import digest

WRITE_PATHS = ('/escape-test', '/etc/escape-test', '/usr/escape-test', '/proc/escape-test')
NETWORK_TARGETS = (('1.1.1.1', 53), ('169.254.169.254', 80), ('93.184.216.34', 443), ('example.com', 443), ('localtest.me', 80))
SECRET_PARTS = ('API_KEY', 'TOKEN', 'PASSWORD', 'SECRET', 'DSN')
FORK_LIMIT = 300
MEMORY_MIB = 512
FLOOD_BYTES = 10 * 1024 * 1024


def program(body):
    # Sample: Return the argv that runs the body with python -I -c.
    return ['python', '-I', '-c', body]


def write_scope(_):
    # Sample: Try four paths outside the work folder. The probe passes when every write fails.
    code = ('import json\nout={}\nfor p in %r:\n    try:\n        open(p,"w").write("x"); out[p]=True\n    except OSError:\n        out[p]=False\nprint(json.dumps(out))\n' % (WRITE_PATHS,))
    return program(code), lambda data, result: (not any(data.values()), data)


def secret_names(_):
    # Sample: List environment names with API_KEY, TOKEN, PASSWORD, SECRET or DSN. The probe passes on an empty list.
    code = 'import json,os\nprint(json.dumps([k for k in os.environ if any(x in k for x in %r)]))\n' % (SECRET_PARTS,)
    return program(code), lambda data, result: (data == [], data)


def network_blocked(targets):
    # Sample: Connect to 1.1.1.1:53, the metadata address, a public IP, example.com and a name that resolves to loopback. The probe passes when every connect fails.
    code = ('import json,socket\nout={}\nfor host,port in %r:\n    try:\n        socket.create_connection((host,port),timeout=3).close(); out[host+":"+str(port)]=True\n'
            '    except OSError:\n        out[host+":"+str(port)]=False\nprint(json.dumps(out))\n' % (tuple(targets),))
    return program(code), lambda data, result: (not any(data.values()), data)


def fork_bomb(_):
    # Sample: Fork up to 300 short-lived children. The probe passes when the program ends and the sandbox still answers.
    code = ('import json,os,time\npids=[]\ntry:\n    for i in range(%d):\n        pid=os.fork()\n        if pid==0:\n            time.sleep(2); os._exit(0)\n        pids.append(pid)\n'
            'except OSError:\n    pass\nfor p in pids:\n    try:\n        os.waitpid(p,0)\n    except OSError:\n        pass\nprint(json.dumps({"forked":len(pids)}))\n' % FORK_LIMIT)
    return program(code), lambda data, result: (isinstance(data, dict) and 'forked' in data, data)


def memory_hog(_):
    # Sample: Touch 512 MiB with a 128 or 256 MiB limit. The probe passes when the program is killed and does not print allocated.
    code = 'b=bytearray(%d*1024*1024)\nfor i in range(0,len(b),4096):\n    b[i]=1\nprint("allocated")\n' % MEMORY_MIB
    return program(code), lambda data, result: (result['state'] == 'FAILED' and b'allocated' not in result['output'], {'state': result['state'], 'exitCode': result['exitCode']})


def output_flood(_):
    # Sample: Print 10 MiB. The probe passes when the result is FAILED with reason output-limit and the kept bytes stay at the cap or below.
    code = 'import sys\nsys.stdout.write("x" * %d)\n' % FLOOD_BYTES
    return program(code), lambda data, result: (result['state'] == 'FAILED' and result['reason'] == 'output-limit', {'reason': result['reason'], 'kept': len(result['output'])})


def host_files(_):
    # Sample: Read /var/run/docker.sock, /root/.ssh/id_rsa, /mnt/c/Users, /etc/shadow and /proc/kcore. The probe passes when none of them can be opened.
    code = ('import json\nout={}\nfor p in ("/var/run/docker.sock","/root/.ssh/id_rsa","/mnt/c/Users","/etc/shadow","/proc/kcore"):\n    try:\n        out[p]=len(open(p,"rb").read(1))>0\n'
            '    except OSError:\n        out[p]=False\nprint(json.dumps(out))\n')
    return program(code), lambda data, result: (not any(data.values()), data)


PROBES = {'write_scope': write_scope, 'secret_names': secret_names, 'network_blocked': network_blocked, 'fork_bomb': fork_bomb,
          'memory_hog': memory_hog, 'output_flood': output_flood, 'host_files': host_files}


def parse(output):
    # Sample: The last line that parses as JSON is the probe answer. No such line returns None.
    for line in reversed(output.decode('utf-8', 'replace').strip().splitlines()):
        try:
            return json.loads(line)
        except ValueError:
            continue
    return None


def run_suite(port, name, probes=None, network_targets=NETWORK_TARGETS, spec=None):
    # Sample: Create one sandbox with 128 MiB. Run each chosen probe. After each probe check the sandbox still answers. Return the receipt with a digest.
    spec = spec or {'memoryMiB': 128, 'seconds': 30}
    sandbox = port.create(spec, 'escape-suite-' + name)
    results = {}
    try:
        for probe in (probes or PROBES):
            argv, check = PROBES[probe](network_targets if probe == 'network_blocked' else None)
            result = port.exec(sandbox, argv, deadline=40)
            data = parse(result['output'])
            try:
                passed, detail = check(data, result)
            except Exception as error:
                passed, detail = False, 'probe answer unreadable: ' + type(error).__name__
            alive = port.exec(sandbox, program('print("alive")'), deadline=20)
            alive_ok = alive['state'] == 'SUCCEEDED' and b'alive' in alive['output']
            results[probe] = {'passed': bool(passed and alive_ok), 'sandboxAliveAfter': alive_ok, 'detail': detail}
    finally:
        port.destroy(sandbox, 'escape-suite-end-' + name)
    receipt = {'adapter': name, 'isolated': bool(getattr(port, 'ISOLATED', False)), 'probes': results,
               'state': 'PASS' if results and all(item['passed'] for item in results.values()) else 'FAIL'}
    receipt['digest'] = digest({key: value for key, value in receipt.items()})
    return receipt
