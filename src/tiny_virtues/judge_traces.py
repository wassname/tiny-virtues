"""Readable full judge traces; raw requests and replies remain unchanged. -- PI/Sol"""
import hashlib
import json
import os
from pathlib import Path
import tempfile


def _json(value):
    return json.dumps(value, ensure_ascii=False, indent=2)


def decision_trace(directory: Path, packet: dict, record: dict) -> dict:
    request = record['request']
    assert request['state'] == packet['request']['state'], packet['id']
    for key, question in packet['request']['questions'].items():
        assert request['questions'][key] == question, (packet['id'], key)
    path = Path(directory) / 'traces' / (packet['id'] + '.md')
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
        try:
            file.write(f"# Complete decision trace: {packet['id']}\n\n")
            file.write('API inputs and returned evidence, not a reconstruction of hidden model reasoning.\n\n')
            file.write(f"Request key: {record['key']}\n\n## Shared state, decoded without cuts\n\n")
            for key, value in request['state'].items():
                file.write(f'### {key}\n\n```text\n')
                if isinstance(value, str):
                    file.write(value)
                else:
                    json.dump(value, file, ensure_ascii=False, indent=2)
                file.write('\n```\n\n')
            file.write('## Complete submitted request\n\n```json\n')
            json.dump(request, file, ensure_ascii=False, indent=2)
            file.write('\n```\n\n## Complete saved record (a response is present only after a call)\n\n```json\n')
            json.dump(record, file, ensure_ascii=False, indent=2)
            file.write('\n```\n\n-- PI/Sol\n')
            file.flush()
            os.fsync(file.fileno())
        except BaseException:
            temporary.unlink()
            raise
    os.replace(temporary, path)
    with path.open('rb') as file:
        sha = hashlib.file_digest(file, 'sha256').hexdigest()
    return dict(path=str(path), sha256=sha, request_key=record['key'])


def generative_trace(directory: Path, record: dict) -> dict:
    lines = ['# Complete generative judge trace', '',
             'The saved request includes the actual judge-note instruction and request settings. Provider-hidden reasoning is unavailable, not invented.', '',
             '## Actual submitted content, without cuts', '', '```text', record['request']['content'], '```', '',
             '## Complete request and return', '', '```json', _json(record), '```', '', '-- PI/Sol', '']
    path = Path(directory) / 'judge_trace.md'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(lines))
    return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
