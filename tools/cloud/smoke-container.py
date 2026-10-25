#!/usr/bin/env python3
"""Reversible non-network container smoke with owned container/volume cleanup."""

import json
import re
import subprocess
import sys
import time
from uuid import uuid4

image = sys.argv[1] if len(sys.argv) > 1 else "amora-core:test"
name = "amora-smoke-" + uuid4().hex[:12]
volume = name + "-data"


def docker(*args):
    return subprocess.run(
        ["docker", *args], check=True, text=True, capture_output=True, timeout=60
    ).stdout.strip()


def ready():
    for attempt in range(30):
        try:
            docker(
                "exec",
                name,
                "python",
                "-c",
                "import urllib.request,json; assert json.load(urllib.request.urlopen('http://127.0.0.1:8765/ready'))['status']=='ready'",
            )
            return
        except subprocess.CalledProcessError:
            if attempt == 29:
                raise
            time.sleep(0.5)


try:
    docker("volume", "create", volume)
    started = time.monotonic()
    docker(
        "run",
        "-d",
        "--name",
        name,
        "--network",
        "none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=16m",
        "-e",
        "AMORA_PUBLIC_ORIGINS=https://amora.example",
        "-e",
        "AMORA_DURABLE_FILESYSTEM=true",
        "-e",
        "AMORA_SECRET_LOCAL_API_TOKEN=isolated-container-security-test-only-token",
        "-v",
        volume + ":/var/lib/amora",
        image,
    )
    ready()
    cold_start_seconds = time.monotonic() - started
    assert docker("exec", name, "id", "-u") != "0"
    docker(
        "exec",
        name,
        "python",
        "-c",
        """
import json,os,urllib.request,urllib.error
from pathlib import Path
status=Path('/proc/self/status').read_text()
assert 'CapEff:\t0000000000000000' in status
assert 'NoNewPrivs:\t1' in status
try:
    Path('/home/amora/security-smoke-write').write_text('unexpected')
except OSError as error:
    import errno
    assert error.errno == errno.EROFS
else:
    raise AssertionError('image filesystem is writable')
origin='http://127.0.0.1:8765'
for path in ['/health','/web/','/avatar/']:
    assert urllib.request.urlopen(origin+path).status==200
try:
    urllib.request.urlopen(origin+'/v1/state')
except urllib.error.HTTPError as error:
    assert error.code==401
else:
    raise AssertionError('private state exposed')
assert json.load(urllib.request.urlopen(origin+'/health')) == {'status': 'ok'}
request=urllib.request.Request(origin+'/v1/web/utterance',data=json.dumps({'text':'Amora, oi.'}).encode(),headers={'Authorization':'Bearer '+os.environ['AMORA_SECRET_LOCAL_API_TOKEN'],'Content-Type':'application/json'})
assert json.load(urllib.request.urlopen(request))['reply_queued']['device_id']=='WEB_SAFARI'
request=urllib.request.Request(origin+'/v1/voice/profiles',headers={'Authorization':'Bearer '+os.environ['AMORA_SECRET_LOCAL_API_TOKEN']})
assert urllib.request.urlopen(request).status==200
""",
    )
    # Issue through the existing local CLI, redeem through the public HTTP path.
    issued = docker("exec", name, "amora", "pair")
    code = re.search(r"pareamento: (\d{6})", issued).group(1)
    docker(
        "exec",
        name,
        "python",
        "-c",
        """
import json,urllib.request,urllib.error
origin='http://127.0.0.1:8765'
request=urllib.request.Request(origin+'/v1/pairing/redeem',data=json.dumps({'code':CODE}).encode(),headers={'Content-Type':'application/json'})
token=json.load(urllib.request.urlopen(request))['token']
assert urllib.request.urlopen(urllib.request.Request(origin+'/v1/web/state',headers={'Authorization':'Bearer '+token})).status==200
for request,expected in [(request,401),(urllib.request.Request(origin+'/v1/operations/state',headers={'Authorization':'Bearer '+token}),403)]:
    try:
        urllib.request.urlopen(request)
    except urllib.error.HTTPError as error:
        assert error.code==expected
    else:
        raise AssertionError('pairing replay or scope bypass')
""".replace("CODE", repr(code)),
    )
    idle_rss_kib = int(
        docker(
            "exec",
            name,
            "python",
            "-c",
            "from pathlib import Path; print(next(line.split()[1] for line in Path('/proc/1/status').read_text().splitlines() if line.startswith('VmRSS:')))",
        )
    )
    mapped_libraries = json.loads(
        docker(
            "exec",
            name,
            "python",
            "-c",
            "import json; from pathlib import Path; print(json.dumps(sorted({line.split()[-1] for line in Path('/proc/1/maps').read_text().splitlines() if '/' in line and '.so' in line})))",
        )
    )
    docker("stop", "-t", "30", name)
    assert docker("inspect", "--format", "{{.State.ExitCode}}", name) == "0"
    docker("start", name)
    ready()
    docker(
        "exec",
        name,
        "python",
        "-c",
        """
import json,os,urllib.request
request=urllib.request.Request('http://127.0.0.1:8765/v1/state',headers={'Authorization':'Bearer '+os.environ['AMORA_SECRET_LOCAL_API_TOKEN']})
assert json.load(urllib.request.urlopen(request))['speech_pending_count']>0
""",
    )
    docker("stop", "-t", "30", name)
    assert docker("inspect", "--format", "{{.State.ExitCode}}", name) == "0"
    print(
        "PASS: non-root, read-only, no capabilities, no-new-privileges, readiness, Web/Avatar, auth, pairing scope/replay, text without voice, SQLite, restart and SIGTERM"
    )
    if len(sys.argv) > 2:
        from pathlib import Path

        Path(sys.argv[2]).write_text(
            json.dumps(
                {
                    "image": image,
                    "cold_start_seconds": cold_start_seconds,
                    "idle_rss_kib": idle_rss_kib,
                    "mapped_libraries": mapped_libraries,
                },
                indent=2,
            )
            + "\n"
        )
finally:
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    subprocess.run(["docker", "volume", "rm", volume], capture_output=True)
