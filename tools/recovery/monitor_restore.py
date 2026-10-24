#!/usr/bin/env python3
"""Monitor an existing restore by reading its status; never open a disk."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import time


def inspect(folder):
    state = json.loads((folder / 'restore-status.json').read_text())
    phase = state['phase']
    problem = None
    if phase == 'failed':
        problem = state.get('error', 'Restore failed')
    elif phase != 'completed':
        try:
            os.kill(int(state['pid']), 0)
        except PermissionError:
            pass  # A non-root observer may see a root job without signalling it.
        except ProcessLookupError:
            problem = 'Restore process is no longer running'
        age = time.time() - datetime.fromisoformat(state['updated_at']).timestamp()
        if age > 180:
            problem = 'Progress has not updated for more than 180 seconds'
    elif not all(state.get(k) for k in ('target_written', 'readback_verified', 'safely_ejected')):
        problem = 'Completion receipt is missing a required acceptance flag'
    result = dict(state, monitor_checked_at=datetime.now().astimezone().isoformat(),
                  monitor_result='needs_attention' if problem else
                  ('completed' if phase == 'completed' else 'running'),
                  monitor_problem=problem)
    out = folder / 'monitor-summary.json'
    temporary = out.with_suffix('.json.partial')
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(out)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--interval', type=int, default=60)
    args = parser.parse_args()
    if args.interval < 10:
        parser.error('Use an interval of at least 10 seconds')
    while True:
        result = inspect(args.folder)
        if not args.watch or result['monitor_result'] != 'running':
            print(json.dumps(result, ensure_ascii=False))
            return
        time.sleep(args.interval)


if __name__ == '__main__':
    main()
