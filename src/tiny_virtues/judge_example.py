"""Render one complete saved Decisions call without credentials or inference. -- PI/OpenAI"""
import hashlib
import json
from html import escape
from pathlib import Path


def response_schema(request):
    probability = dict(type='number', minimum=0, maximum=1)
    answers = {}
    for name, question in request['questions'].items():
        if question['type'] == 'noul':
            fields = dict(type=dict(const='noul'), noul=probability)
        elif question['type'] == 'choice':
            options = list(question['criteria'])
            fields = dict(type=dict(const='choice'), choice=dict(enum=options),
                          probabilities=dict(type='object', required=options,
                              properties={key: probability for key in options}, additionalProperties=False),
                          confidence=probability)
        else:
            raise ValueError(question['type'])
        answers[name] = dict(type='object', required=list(fields), properties=fields, additionalProperties=False)
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema',
            'title': 'Local documentation schema derived from this saved request, not sent to the API',
            'type': 'object', 'required': ['id', 'model', 'provider', 'usage', 'answers'],
            'properties': dict(id=dict(type='string'), model=dict(type='string'), provider=dict(type='string'),
                usage=dict(type='object', required=['input_tokens', 'output_tokens', 'cost'], properties=dict(
                    input_tokens=dict(type='integer', minimum=0), output_tokens=dict(type='integer', minimum=0),
                    cost=dict(type='number', minimum=0))),
                answers=dict(type='object', required=list(answers), properties=answers, additionalProperties=False))}


def readable_request(request):
    paragraph = lambda text: '\n'.join('> ' + line if line else '>' for line in escape(text, quote=False).replace('\n', '<br>\n').split('\n'))
    lines = ['<details>', '<summary>Read the complete prompt as wrapped text</summary>', '']
    for key, title in [('task', 'Shared task'), ('agent', 'Person being judged'), ('story', 'Passage')]:
        lines += [f'### {title}', '', paragraph(request['state'][key]), '']
    for index, (name, question) in enumerate(request['questions'].items(), 1):
        lines += [f'### Question {index}: `{name}`', '', paragraph(question['instructions']), '']
        if question['type'] == 'noul':
            lines += ['Answer type: yes/no probability (`noul`).', '']
        else:
            assert question['type'] == 'choice'
            lines += ['Answer options, in the order sent:', '', '<ul>']
            for option, description in question['criteria'].items():
                lines += [f'<li><code>{escape(option)}</code>: {escape(description)}</li>']
            lines += ['</ul>', '']
    return '\n'.join(lines + ['</details>', ''])


def appendix(root: Path):
    directory = root / 'data/judges/examples/decider_v12'
    manifest = json.loads((directory / 'manifest.json').read_text())
    request = json.loads((directory / 'request.json').read_text())
    response = json.loads((directory / 'response.json').read_text())
    schema = json.loads((directory / 'response.schema.json').read_text())
    for name in ('request', 'response'):
        value = request if name == 'request' else response
        assert hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest() == manifest[f'{name}_sha256']
    assert schema == response_schema(request)
    assert list(request['questions']) == list(response['answers'])
    lines = ['## Appendix: one complete judge call', '',
        f'This is one real dataset-labelling request for `{manifest["case_id"]}`, selected as {manifest["selection"]}. '
        f'It contains all {len(request["questions"]) - 1} scored questions in this request and the unscored `debug_word` question. '
        'A case uses several requests; questions in other requests are not part of this call.', '',
        f'Requested model: `{request["model"]}`. Served model: `{response["model"]}`. '
        f'Scored question version: `{manifest["qsha"]}`.', '',
        'This endpoint takes a structured JSON request rather than chat messages. '
        'The prompt text is inside `state` and `questions`: the shared task, person, passage and each question’s instructions. '
        'The client sends it to `https://openrouter.ai/api/alpha/decisions`.', '',
        'No separate system message, chat template, token IDs or special-token delimiters are supplied by this client. '
        'The provider\'s internal system prompt, serialization and special tokens are not exposed by the Decisions API, '
        'so they cannot be reproduced here. Authentication headers are not included.', '',
        'Question names use `+` for showing a virtue, `-` for showing a named vice, and `~` for acting against an item. '
        'These mark question directions, not score signs. '
        'The diagnostic question’s access to other questions is not yet verified.', '',
        'The full prompt below uses blockquotes so long lines wrap. Line breaks in the saved strings are shown as line breaks. '
        'The request link opens the exact JSON sent to the API. '
        'The question object is the schema actually sent; the separate JSON Schema documents the expected reply and was not sent to the provider. '
        'It checks structure and bounds, not probability calibration or moral correctness.', '',
        '[Request](data/judges/examples/decider_v12/request.json) · '
        '[Reply schema](data/judges/examples/decider_v12/response.schema.json) · '
        '[Reply](data/judges/examples/decider_v12/response.json) · '
        '[Selection and hashes](data/judges/examples/decider_v12/manifest.json)', '',
        readable_request(request), '',
        '<details>', '<summary>Reply schema and raw decoded reply</summary>', '']
    for title, value in [('Reply JSON Schema (locally derived)', schema), ('Complete decoded provider reply', response)]:
        lines += [f'### {title}', '', '```json', json.dumps(value, ensure_ascii=False, indent=2), '```', '']
    lines += ['</details>', '']
    return '\n'.join(lines)
