#!/usr/bin/env python3
"""Local background preparation after checksum-verified source transfer.

Builds/checks regular files only. It never writes a physical disk or changes
machine identity. Use private local storage: snapshots contain machine secrets.
"""
import argparse
import datetime
import gzip
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from adapt_image import MIB, sha256, sparse_write
from assemble_snapshot import geometry


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage', type=Path, required=True)
    p.add_argument('--source-name', required=True)
    p.add_argument('--target-bytes', type=int, required=True)
    p.add_argument('--checker-image', default='mctivity-recovery-check:20261024')
    args = p.parse_args()
    os.umask(0o077)
    args.stage.chmod(0o700)
    source = args.stage / args.source_name
    candidate = args.stage / 'candidate'
    inputs = args.stage / 'build-input'
    tools = Path(__file__).resolve().parent

    def event(phase, **kw):
        data = dict(phase=phase, updated_at=datetime.datetime.now().astimezone().isoformat(), pid=os.getpid(), **kw)
        temp = args.stage / 'preparation-status.json.partial'
        temp.write_text(json.dumps(data, indent=2) + '\n')
        temp.replace(args.stage / 'preparation-status.json')

    def command(name, cmd, accepted=(0,)):
        event(name)
        with (args.stage / (name + '.log')).open('xb') as out:
            result = subprocess.run(list(map(str, cmd)), stdout=out, stderr=subprocess.STDOUT)
        if result.returncode not in accepted:
            raise RuntimeError(name + ' failed; see log')

    def docker(name, command_args, accepted=(0,), readonly=False):
        command(name, ['docker', 'run', '--rm', '--network', 'none',
                      '--mount', 'type=bind,src=' + str(args.stage) + ',dst=/work' + (',readonly' if readonly else ''),
                      '--mount', 'type=bind,src=' + str(tools) + ',dst=/tools,readonly',
                      '--tmpfs', '/tmp:rw,size=64m', '--entrypoint', command_args[0],
                      args.checker_image, *command_args[1:]], accepted)

    try:
        while True:
            event('waiting_for_transfer')
            f = source / 'fetch-status.json'
            if f.exists():
                result = json.loads(f.read_text())
                if result['phase'] == 'failed':
                    raise RuntimeError('Source transfer failed; see fetch status')
                if result['phase'] == 'completed':
                    break
            time.sleep(60)
        candidate.mkdir()
        inputs.mkdir()
        for name in ['disk-head.bin', 'disk-tail.bin', 'swap-header.bin', 'checksums.json', 'source-layout.json']:
            shutil.copy2(source / name, inputs / name)
        report = geometry((source / 'disk-head.bin').read_bytes(), args.target_bytes)[3]
        (args.stage / 'planned-geometry.json').write_text(json.dumps(report, indent=2) + '\n')
        event('unpacking_partitions')
        with gzip.open(source / 'root.qcow2.gz', 'rb') as inp, (source / 'root.qcow2').open('xb') as out:
            shutil.copyfileobj(inp, out, 8 * MIB)
        with gzip.open(source / 'efi.img.gz', 'rb') as inp, (inputs / 'efi.img').open('xb') as out:
            offset = 0
            while True:
                data = inp.read(8 * MIB)
                if not data:
                    break
                sparse_write(out, offset, data)
                offset += len(data)
            out.truncate(offset)
        docker('convert_root', ['e2image', '-r', '/work/' + args.source_name + '/root.qcow2', '/work/candidate/root.img'])
        # A frozen mounted source can look clean but retain timestamps that
        # resize2fs requires a full check to refresh. Do not let preen skip it.
        docker('recover_root', ['e2fsck', '-p', '-f', '/work/candidate/root.img'], (0, 1))
        docker('recover_efi', ['fsck.fat', '-a', '/work/build-input/efi.img'], (0, 1))
        # 4096-byte ext4 blocks are mandatory for this reviewed source layout.
        docker('root_block_size', ['sh', '-ceu', "dumpe2fs -h /work/candidate/root.img 2>/dev/null | grep -Eq '^Block size: +4096$'"])
        with (candidate / 'root.img').open('r+b') as f:
            f.truncate(report['root_bytes'])
        docker('grow_root', ['resize2fs', '/work/candidate/root.img', str(report['root_bytes'] // 4096)])
        docker('check_grown_root', ['e2fsck', '-f', '-n', '/work/candidate/root.img'], readonly=True)
        event('compressing_efi')
        with (inputs / 'efi.img').open('rb') as inp, gzip.open(inputs / 'efi.img.gz', 'xb', compresslevel=1) as out:
            shutil.copyfileobj(inp, out, 8 * MIB)
        command('assemble_image', [os.sys.executable, tools / 'assemble_snapshot.py', '--source', inputs,
                                  '--root', candidate / 'root.img', '--target-bytes', args.target_bytes, '--output', candidate])
        docker('independent_validation', ['python3', '/tools/validate_image.py', '/work/candidate'], readonly=True)
        manifest = json.loads((candidate / 'manifest.json').read_text())
        receipt = {'result': 'passed', 'image_sha256': manifest['adapted_raw_sha256'],
                   'filesystem_metadata_recovered': True, 'root_grown': True,
                   'checks': {k: True for k in ['gpt', 'root_matches_manifest', 'root_e2fsck_readonly',
                                               'efi_matches_manifest', 'efi_fat_readonly', 'x64_fallback_efi', 'swap_mkswap_reference']}}
        (args.stage / 'validation-receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
        (candidate / 'adapted.img').chmod(0o400)
        (candidate / 'root.img').chmod(0o400)
        event('completed', image=str(candidate / 'adapted.img'), image_bytes=args.target_bytes,
              image_sha256=manifest['adapted_raw_sha256'], target_written=False,
              identity_policy='Replacement disk for the same source machine; preserve identity')
    except BaseException as exc:
        event('failed', error=repr(exc))
        raise


if __name__ == '__main__':
    main()
