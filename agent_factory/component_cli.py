"""CLI for reusable component versions and consumer bindings."""

import json
from pathlib import Path

from .components import ComponentService


def add_commands(commands, provider_options):
    root = commands.add_parser('components', help='组件目录、按需复用/更新、版本切换和回退')
    operations = root.add_subparsers(dest='component_command', required=True)
    operations.add_parser('list')
    history = operations.add_parser('history')
    history.add_argument('--slot')
    for name in ['plan', 'ensure']:
        command = operations.add_parser(name)
        provider_options(command)
        command.add_argument('--runner', choices=['local', 'docker'])
        source = command.add_mutually_exclusive_group(required=True)
        source.add_argument('--description')
        source.add_argument('--request-file', type=Path)
        command.add_argument('--component')
        command.add_argument('--slot')
        command.add_argument('--task')
        command.add_argument('--rule', action='append')
        command.add_argument('--algorithm')
        command.add_argument('--contract-version')
        command.add_argument('--base-version')
        command.add_argument('--action', choices=['auto', 'create', 'update', 'rewrite'])
        command.add_argument('--parameters', help='JSON 配置对象，例如 {"required_fields":["name"]}')
        command.add_argument('--no-activate', action='store_true', default=None)
        command.add_argument('--allow-contract-change', action='store_true', default=None)
        command.add_argument('--revision', type=int)
        command.add_argument('--request-id')
    imported = operations.add_parser('import')
    imported.add_argument('--run', required=True)
    imported.add_argument('--component')
    imported.add_argument('--runner', choices=['local', 'docker'], default='docker')
    activate = operations.add_parser('activate')
    activate.add_argument('slot')
    activate.add_argument('version')
    activate.add_argument('--revision', type=int, required=True)
    activate.add_argument('--parameters')
    activate.add_argument('--allow-contract-change', action='store_true')
    activate.add_argument('--runner', choices=['local', 'docker'], default='docker')
    rollback = operations.add_parser('rollback')
    rollback.add_argument('slot')
    rollback.add_argument('--revision', type=int, required=True)
    rollback.add_argument('--runner', choices=['local', 'docker'], default='docker')
    invoke = operations.add_parser('invoke')
    invoke.add_argument('slot')
    invoke.add_argument('--input', type=Path, required=True)
    invoke.add_argument('--revision', type=int)
    invoke.add_argument('--contract-version')
    invoke.add_argument('--no-fallback', action='store_true')
    invoke.add_argument('--runner', choices=['local', 'docker'], default='docker')
    draft = operations.add_parser('draft-invoke', help='显式试用未完成业务验证的候选')
    draft.add_argument('--run', required=True)
    draft.add_argument('--input', type=Path, required=True)
    draft.add_argument('--runner', choices=['local', 'docker'], default='docker')
    feedback = operations.add_parser('feedback')
    feedback.add_argument('slot')
    feedback.add_argument('--kind', choices=['data_issue', 'execution_error', 'quality_issue', 'new_requirement'], required=True)
    feedback.add_argument('--summary', required=True)
    feedback.add_argument('--version')


def execute(arguments, workspace):
    service = ComponentService(workspace, getattr(arguments, 'provider', 'mock'),
                               getattr(arguments, 'model', None), getattr(arguments, 'base_url', None),
                               getattr(arguments, 'runner', None))
    operation = arguments.component_command
    if operation == 'list':
        return service.catalog()
    if operation == 'history':
        return {'events': service.store.history(arguments.slot)}
    if operation in {'plan', 'ensure'}:
        request = json.loads(arguments.request_file.read_text()) if arguments.request_file else {}
        mapping = {'rule': 'rules', 'revision': 'expected_revision'}
        for key in ['description', 'component', 'slot', 'task', 'rule', 'algorithm', 'contract_version',
                    'base_version', 'action', 'revision', 'request_id', 'allow_contract_change']:
            value = getattr(arguments, key)
            if value is not None:
                request[mapping.get(key, key)] = value
        if arguments.parameters is not None:
            request['parameters'] = json.loads(arguments.parameters)
        if arguments.no_activate:
            request['activate'] = False
        return getattr(service, operation)(request)
    if operation == 'draft-invoke':
        from .drafts import invoke_draft
        return invoke_draft(workspace, arguments.run, json.loads(arguments.input.read_text()), arguments.runner)
    if operation == 'import':
        return service.import_run(arguments.run, arguments.component)
    if operation == 'activate':
        return service.activate(arguments.slot, arguments.version, arguments.revision,
                                json.loads(arguments.parameters) if arguments.parameters else None,
                                arguments.allow_contract_change)
    if operation == 'rollback':
        return service.rollback(arguments.slot, arguments.revision)
    if operation == 'invoke':
        return service.invoke(arguments.slot, json.loads(arguments.input.read_text()), arguments.revision,
                              arguments.contract_version, not arguments.no_fallback)
    return service.feedback(arguments.slot, {'kind': arguments.kind, 'summary': arguments.summary,
                                             'version': arguments.version})
