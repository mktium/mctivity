#!/usr/bin/env python3
"""Capture a small ext4 root into private RAM, with bounded filesystem freeze.

Run as root on the source. Never writes source block devices. Only the HMI is
temporarily stopped; motion services must already be stopped. The output is a
filesystem clone (allocated data and metadata), not deleted/free-space data.
"""
import argparse
import datetime
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import pwd
import select
import signal
import subprocess
import sys

FIFREEZE = 0xC0045877
FITHAW = 0xC0045878


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(8 * 1024**2), b''):
            h.update(block)
    return h.hexdigest()


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--disk', default='/dev/sda')
    p.add_argument('--expected-bytes', type=int, required=True)
    p.add_argument('--owner', required=True)
    p.add_argument('--execute', action='store_true')
    args = p.parse_args()
    require(os.geteuid() == 0 and args.execute, 'Explicit root execution required')
    require(args.disk == '/dev/sda', 'This capture supports the reviewed sda layout only')
    require(str(args.output).startswith('/run/') and not args.output.exists(), 'Use a new private /run output')
    require(int(run('/usr/sbin/blockdev', '--getsize64', args.disk)) == args.expected_bytes, 'Source capacity changed')
    require(run('/usr/bin/findmnt', '-n', '-o', 'SOURCE,FSTYPE', '/') == '/dev/sda2 ext4', 'Unexpected root filesystem')
    for service in ['ethercat.service', 'mctivity-motiond.service']:
        require(subprocess.run(['systemctl', 'is-active', '--quiet', service]).returncode != 0,
                'Motion/fieldbus must already be stopped')
    memory = {line.split(':')[0]: int(line.split()[1]) * 1024 for line in Path('/proc/meminfo').read_text().splitlines() if ':' in line and line.split()[1].isdigit()}
    root = os.statvfs('/')
    used = (root.f_blocks - root.f_bfree) * root.f_frsize
    require(used < 2800 * 1024**2 and memory['MemAvailable'] > 3200 * 1024**2,
            'Insufficient RAM margin for bounded snapshot')
    require(memory['SwapFree'] > 2 * 1024**3, 'Require at least 2 GiB emergency swap margin')
    account = pwd.getpwnam(args.owner)
    os.umask(0o077)
    args.output.mkdir(mode=0o700)
    args.output.chmod(0o711)  # Status is readable by name; snapshot stays private.
    ram = args.output / 'image'
    ram.mkdir(mode=0o700)
    subprocess.run(['mount', '-t', 'tmpfs', '-o', 'size=3G,mode=0700,nosuid,nodev,noexec', 'tmpfs', str(ram)], check=True)
    state = {'phase': 'preparing', 'source_bytes': args.expected_bytes, 'pid': os.getpid(), 'root_frozen': False}

    def event(phase, **kw):
        state.update(kw, phase=phase, updated_at=datetime.datetime.now().astimezone().isoformat())
        tmp = args.output / 'status.json.partial'
        tmp.write_text(json.dumps(state, indent=2) + '\n')
        tmp.replace(args.output / 'status.json')
        (args.output / 'status.json').chmod(0o644)

    was_hmi_active = subprocess.run(['systemctl', 'is-active', '--quiet', 'mctivity-hmi.service']).returncode == 0
    efi_options = run('findmnt', '-n', '-o', 'OPTIONS', '/boot/efi').split(',')
    efi_was_rw = 'rw' in efi_options
    rootfd = os.open('/', os.O_RDONLY)
    frozen = False
    watchdog = None
    stopfd = None
    error = None
    try:
        event('preparing')
        # Execute before freezing to fault in the executable/libraries and apply
        # any pending atime updates. A first exec on frozen root can block.
        subprocess.run(['/usr/sbin/e2image'], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=10)
        metadata = {'sfdisk': json.loads(run('/usr/sbin/sfdisk', '--json', args.disk)),
                    'blkid': run('/usr/sbin/blkid', '/dev/sda1', '/dev/sda2', '/dev/sda3'),
                    'fstab': Path('/etc/fstab').read_text(), 'uname': run('uname', '-r')}
        (ram / 'source-layout.json').write_text(json.dumps(metadata, indent=2) + '\n')
        with open(args.disk, 'rb', buffering=0) as source:
            (ram / 'disk-head.bin').write_bytes(source.read(1024**2))
            source.seek(args.expected_bytes - 1024**2)
            (ram / 'disk-tail.bin').write_bytes(source.read(1024**2))
        (ram / 'swap-header.bin').write_bytes(Path('/dev/sda3').open('rb').read(4096))
        if was_hmi_active:
            subprocess.run(['systemctl', 'stop', 'mctivity-hmi.service'], check=True, timeout=30)
        if efi_was_rw:
            subprocess.run(['mount', '-o', 'remount,ro', '/boot/efi'], check=True, timeout=20)
        event('capturing_efi')
        with open('/dev/sda1', 'rb', buffering=0) as source, gzip.open(ram / 'efi.img.gz', 'xb', compresslevel=1) as out:
            for data in iter(lambda: source.read(8 * 1024**2), b''):
                out.write(data)
        # Fork before freezing. The independent watchdog only needs an already
        # open directory descriptor and memory; it does not write the root FS.
        readfd, stopfd = os.pipe()
        watchdog = os.fork()
        if watchdog == 0:
            os.close(stopfd)
            ready, _, _ = select.select([readfd], [], [], 300)
            clean_stop = bool(ready) and os.read(readfd, 4) == b'stop'
            if not clean_stop:
                try:
                    fcntl.ioctl(rootfd, FITHAW, 0)
                except OSError:
                    pass
                (args.output / 'watchdog-expired').write_text('Capture invalid: timeout or parent exit\n')
                if efi_was_rw:
                    subprocess.run(['mount', '-o', 'remount,rw', '/boot/efi'], timeout=20)
                if was_hmi_active:
                    subprocess.run(['systemctl', 'start', 'mctivity-hmi.service'], timeout=30)
            os._exit(0)
        os.close(readfd)
        event('freezing_root')
        fcntl.ioctl(rootfd, FIFREEZE, 0)
        frozen = True
        event('capturing_root', root_frozen=True)
        # -f bypasses the mounted-rw test ONLY while FIFREEZE prevents writes.
        with (args.output / 'e2image.log').open('xb') as log:
            # libext2fs otherwise creates /.ismount-test-file to probe root,
            # which deadlocks on a frozen filesystem. This documented-in-source
            # library switch bypasses only that probe, after FIFREEZE succeeds.
            environment = dict(os.environ, EXT2FS_PRETEND_RW_MOUNT='1')
            subprocess.run(['/usr/sbin/e2image', '-aQf', '/dev/sda2', str(ram / 'root.qcow2')],
                           stdout=log, stderr=subprocess.STDOUT, check=True, timeout=240,
                           env=environment)
        require(not (args.output / 'watchdog-expired').exists(), 'Emergency thaw invalidated capture')
    except BaseException as exc:
        error = repr(exc)
    finally:
        if frozen:
            try:
                fcntl.ioctl(rootfd, FITHAW, 0)
            except OSError as exc:
                error = error or ('Thaw failed: ' + repr(exc))
        os.close(rootfd)
        if stopfd is not None:
            try:
                os.write(stopfd, b'stop')
            except BrokenPipeError:
                pass  # An expired watchdog has already thawed/restored services.
            os.close(stopfd)
        if watchdog:
            os.waitpid(watchdog, 0)
        if efi_was_rw:
            result = subprocess.run(['mount', '-o', 'remount,rw', '/boot/efi'], timeout=20)
            if result.returncode:
                error = error or 'EFI rw restoration failed'
        if was_hmi_active:
            result = subprocess.run(['systemctl', 'start', 'mctivity-hmi.service'], timeout=30)
            if result.returncode:
                error = error or 'HMI restart failed'
        event('failed' if error else 'hashing_snapshot', root_frozen=False, error=error)
    if error:
        raise RuntimeError(error)
    checksums = {f.name: {'bytes': f.stat().st_size, 'sha256': sha(f)} for f in ram.iterdir() if f.is_file()}
    (ram / 'checksums.json').write_text(json.dumps(checksums, indent=2) + '\n')
    for item in [*ram.iterdir(), ram, *args.output.iterdir(), args.output]:
        os.chown(item, account.pw_uid, account.pw_gid)
    event('completed', root_frozen=False, snapshot_files=checksums,
          hmi_restored=was_hmi_active, private_ram_mount=str(ram))
    os.chown(args.output / 'status.json', account.pw_uid, account.pw_gid)
    print(json.dumps(state), flush=True)


if __name__ == '__main__':
    main()
