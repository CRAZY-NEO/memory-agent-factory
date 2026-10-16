"""Provider settings and data-only project configuration shared by entry points.

API credentials remain in the process environment; settings returned here never
include them. A project .env is configuration data, not a shell script.
"""

import os
import re
from pathlib import Path
from urllib.parse import urlsplit

PROVIDERS = ('mock', 'ollama', 'deepseek')
REAL_PROVIDERS = frozenset({'ollama', 'deepseek'})
DEFAULT_DEEPSEEK_MODEL = 'deepseek-v4-pro'
DEFAULT_DEEPSEEK_URL = 'https://api.deepseek.com'


def validate_deepseek_url(base_url):
    """Allow credentials only to the official HTTPS API, without redirects."""
    parsed = urlsplit(base_url)
    try:
        valid = (parsed.scheme == 'https' and parsed.hostname == 'api.deepseek.com'
                 and parsed.port in {None, 443} and not parsed.username and not parsed.password
                 and parsed.path in {'', '/', '/v1', '/v1/'}
                 and not parsed.query and not parsed.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('DEEPSEEK_BASE_URL must be the official HTTPS API URL (optionally /v1)')
    return base_url.rstrip('/')


def resolve_settings(provider, model=None, base_url=None):
    """Resolve explicit options before provider-specific environment/defaults."""
    if provider not in PROVIDERS:
        raise ValueError('Unsupported provider')
    if provider == 'deepseek':
        selected_model = model or os.getenv('DEEPSEEK_MODEL') or DEFAULT_DEEPSEEK_MODEL
        selected_url = base_url or os.getenv('DEEPSEEK_BASE_URL') or DEFAULT_DEEPSEEK_URL
        return selected_model, validate_deepseek_url(selected_url)
    if provider == 'ollama':
        return (model or os.getenv('OLLAMA_CHAT_MODEL') or 'qwen3.5:2b',
                (base_url or os.getenv('OLLAMA_URL') or 'http://localhost:11434').rstrip('/'))
    return model or 'mock-template-v1', (base_url or 'http://localhost:11434').rstrip('/')


def _dotenv_value(value, line_number):
    """Read a single-line dotenv value without interpolation or shell expansion."""
    value = value.strip()
    if not value or value[0] not in {'\"', "'"}:
        return re.split(r'\s+#', value, maxsplit=1)[0].rstrip()
    quote = value[0]
    result = []
    offset = 1
    escapes = {'n': '\n', 'r': '\r', 't': '\t', '\\': '\\', '\"': '\"'}
    while offset < len(value):
        char = value[offset]
        if char == quote:
            trailing = value[offset + 1:].strip()
            if trailing and not trailing.startswith('#'):
                break
            return ''.join(result)
        if char == '\\' and offset + 1 < len(value):
            following = value[offset + 1]
            if quote == '\"' and following in escapes:
                result.append(escapes[following])
                offset += 2
                continue
            if quote == "'" and following in {"'", '\\'}:
                result.append(following)
                offset += 2
                continue
        result.append(char)
        offset += 1
    raise ValueError(f'.env line {line_number}: use a complete single-line quoted value')


def load_project_env(workspace):
    """Load .env once per call, preserving exported environment values.

    Supported syntax: KEY=value, optional export, quoted single-line values and
    comments. Variable references and command substitutions remain literal text.
    Validation is atomic and errors never include a configuration value.
    """
    if os.getenv('FACTORY_DISABLE_DOTENV') == '1':
        return
    path = Path(workspace) / '.env'
    if not path.exists():
        return
    values = {}
    for number, raw_line in enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:].lstrip()
        key, separator, value = line.partition('=')
        key = key.strip()
        if not separator or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key) or '\0' in value:
            raise ValueError(f'.env line {number}: expected KEY=value')
        values[key] = _dotenv_value(value, number)
    for key, value in values.items():
        os.environ.setdefault(key, value)
