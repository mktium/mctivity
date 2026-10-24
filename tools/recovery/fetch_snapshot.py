#!/usr/bin/env python3
"""Fetch a completed private RAM snapshot over an existing SSH key connection."""
import argparse
import datetime
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import time


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--host', required=True)
    p.add_argument('--remote', required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(mode=0o700, parents=True, exist_ok=True)
    args.output.chmod(0o700)
    ssh = ['ssh', '-o', 'BatchMode=yes', '-o', 'ControlMaster=no', '-o', 'ControlPath=none', args.host]

    def event(phase, **kw):
        data = dict(phase=phase, updated_at=datetime.datetime.now().astimezone().isoformat(), **kw)
        tmp = args.output / 'fetch-status.json.partial'
        tmp.write_text(json.dumps(data, indent=2) + '\n')
        tmp.replace(args.output / 'fetch-status.json')

    try:
        deadline = time.monotonic() + 660
        while True:
            event('waiting_for_capture')
            try:
                source = json.loads(subprocess.check_output(ssh + ['cat', args.remote + '/status.json'], timeout=20))
                if source['phase'] == 'failed':
                    raise RuntimeError('Source capture failed: ' + str(source.get('error')))
                if source['phase'] == 'completed' and not source['root_frozen']:
                    break
            except subprocess.TimeoutExpired:
                pass
            if time.monotonic() > deadline:
                raise TimeoutError('Source did not complete capture before deadline')
            time.sleep(60)
        (args.output / 'source-status.json').write_text(json.dumps(source, indent=2) + '\n')
        sums = json.loads(subprocess.check_output(ssh + ['cat', args.remote + '/image/checksums.json'], timeout=20))
        (args.output / 'checksums.json').write_text(json.dumps(sums, indent=2) + '\n')
        for name, expected in sums.items():
            if Path(name).name != name:
                raise RuntimeError('Invalid snapshot filename')
            event('transferring', file=name, expected_bytes=expected['bytes'])
            destination = args.output / (name + '.gz' if name == 'root.qcow2' else name)
            temporary = destination.with_name(destination.name + '.partial')
            if destination.exists() or temporary.exists():
                raise RuntimeError('Refusing to overwrite transfer file')
            command = ssh + (['gzip', '-c', '-1', args.remote + '/image/' + name]
                             if name == 'root.qcow2' else ['cat', args.remote + '/image/' + name])
            with temporary.open('xb') as out:
                subprocess.run(command, stdout=out, check=True, timeout=3600)
            h, count = hashlib.sha256(), 0
            opener = gzip.open if name == 'root.qcow2' else open
            with opener(temporary, 'rb') as f:
                for block in iter(lambda: f.read(8 * 1024**2), b''):
                    h.update(block)
                    count += len(block)
            if h.hexdigest() != expected['sha256'] or count != expected['bytes']:
                raise RuntimeError('Transferred snapshot checksum mismatch: ' + name)
            temporary.replace(destination)
        event('completed', files=sums, remote_cleanup_pending=True)
    except BaseException as exc:
        event('failed', error=repr(exc))
        raise


if __name__ == '__main__':
    main()
