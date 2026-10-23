#!/usr/bin/env python3
"""Reversible non-network container smoke with owned container/volume cleanup."""

import json
import subprocess
import sys
import time
from uuid import uuid4

image = sys.argv[1] if len(sys.argv) > 1 else "amora-core:test"
name = "amora-smoke-" + uuid4().hex[:12]
volume = name + "-data"


def docker(*args):
    return subprocess.run(
        ["docker", *args], check=True, text=True, capture_output=True
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
    docker(
        "run",
        "-d",
        "--name",
        name,
        "--network",
        "none",
        "-e",
        "AMORA_DURABLE_FILESYSTEM=true",
        "-e",
        "AMORA_SECRET_LOCAL_API_TOKEN=isolated-container-test-only",
        "-v",
        volume + ":/var/lib/amora",
        image,
    )
    ready()
    assert docker("exec", name, "id", "-u") != "0"
    docker(
        "exec",
        name,
        "python",
        "-c",
        """
import json,os,urllib.request,urllib.error
origin='http://127.0.0.1:8765'
for path in ['/health','/web/','/avatar/']:
    assert urllib.request.urlopen(origin+path).status==200
try:
    urllib.request.urlopen(origin+'/v1/state')
except urllib.error.HTTPError as error:
    assert error.code==401
else:
    raise AssertionError('private state exposed')
request=urllib.request.Request(origin+'/v1/web/utterance',data=json.dumps({'text':'Amora, oi.'}).encode(),headers={'Authorization':'Bearer '+os.environ['AMORA_SECRET_LOCAL_API_TOKEN'],'Content-Type':'application/json'})
assert json.load(urllib.request.urlopen(request))['reply_queued']['device_id']=='WEB_SAFARI'
""",
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
        "PASS: non-root, readiness, Web/Avatar, authenticated text, persistent queue, restart and SIGTERM"
    )
finally:
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    subprocess.run(["docker", "volume", "rm", volume], capture_output=True)
