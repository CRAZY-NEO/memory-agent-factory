"""Read bounded public GitHub source snapshots without running repository code."""

import hashlib
import io
import json
import os
import re
import tarfile
from pathlib import Path, PurePosixPath

import httpx

PERMITTED_LICENSES = {'MIT', 'Apache-2.0', 'BSD-2-Clause', 'BSD-3-Clause', 'ISC', '0BSD', 'Unlicense', 'CC0-1.0'}
REPOSITORY = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$')


class GitHubSources:
    """Public repository search and pinned tar snapshots; authentication stays on api.github.com."""

    def __init__(self, token=None, transport=None):
        self.token = token if token is not None else os.getenv('GITHUB_TOKEN', '')
        self.transport = transport
        self.trace = []

    def _get(self, url, *, params=None, limit=2 * 1024 * 1024):
        parsed = httpx.URL(url)
        if parsed.scheme != 'https' or parsed.host not in {'api.github.com', 'codeload.github.com'}:
            raise ValueError('GitHub source request outside allowed hosts')
        headers = {'User-Agent': 'memory-agent-factory', 'Accept': 'application/vnd.github+json',
                   'X-GitHub-Api-Version': '2022-11-28'}
        if parsed.host == 'api.github.com' and self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        self.trace.append({'url': str(parsed), 'params': params or {}})
        with httpx.Client(timeout=30, trust_env=False, transport=self.transport) as client:
            with client.stream('GET', url, params=params, headers=headers, follow_redirects=False) as response:
                if response.status_code in {403, 429}:
                    reset = response.headers.get('x-ratelimit-reset', 'unknown')
                    raise RuntimeError(f'GitHub request restricted/rate limited; reset={reset}. No cached or mock fallback.')
                if response.is_error or response.is_redirect:
                    raise RuntimeError(f'GitHub source request failed: HTTP {response.status_code}')
                content = bytearray()
                for part in response.iter_bytes():
                    content.extend(part)
                    if len(content) > limit:
                        raise ValueError('GitHub response exceeds download budget')
                return bytes(content)

    def search(self, queries, limit=5):
        if not 1 <= len(queries) <= 3 or not 1 <= limit <= 10:
            raise ValueError('Invalid GitHub search budget')
        found, excluded = {}, []
        for index, query in enumerate(queries):
            if not isinstance(query, str) or not 1 <= len(query) <= 180:
                raise ValueError('Search query must be 1–180 characters')
            # Exact method names may occur in READMEs; the broader family search
            # uses repository names to avoid being dominated by popular applications.
            scope = 'in:name' if len(queries) > 1 and index == len(queries) - 1 else 'in:readme'
            expression = query + ' ' + scope + ' language:Python archived:false fork:false is:public'
            body = json.loads(self._get('https://api.github.com/search/repositories',
                                       params={'q': expression, 'per_page': 10, 'sort': 'stars'}))
            for item in body.get('items', []):
                name = item.get('full_name', '')
                license_id = (item.get('license') or {}).get('spdx_id')
                if not REPOSITORY.fullmatch(name) or item.get('private') or license_id not in PERMITTED_LICENSES:
                    excluded.append({'repository': name, 'reason': 'private, invalid name or unsupported/unknown license'})
                    continue
                found.setdefault(name, {'full_name': name, 'description': (item.get('description') or '')[:600],
                                       'license': license_id, 'stars': item.get('stargazers_count', 0),
                                       'url': 'https://github.com/' + name})
        words = set(re.findall(r'[a-z0-9]+', ' '.join(queries).lower())) - {'python', 'library', 'algorithm', 'algorithms'}
        ordered = sorted(found.values(), key=lambda row: (
            -sum(word in row['full_name'].lower() for word in words),
            -sum(word in row['description'].lower() for word in words), -row['stars'], row['full_name']))
        named = [row for row in ordered if any(word in row['full_name'].lower() for word in words)]
        if named:
            ordered = named
        return {'repositories': ordered[:limit], 'excluded': excluded[:20],
                'search_trace': self.trace.copy()}

    def download(self, name, directory):
        if not REPOSITORY.fullmatch(name):
            raise ValueError('Expected a public owner/repository name')
        metadata = json.loads(self._get('https://api.github.com/repos/' + name))
        license_id = (metadata.get('license') or {}).get('spdx_id')
        if metadata.get('private') or license_id not in PERMITTED_LICENSES:
            raise ValueError('Automatic adaptation requires a public repository with a supported license')
        # Resolve HEAD once, then all source comes from this exact immutable commit.
        commits = json.loads(self._get('https://api.github.com/repos/' + name + '/commits',
                                      params={'per_page': 1}))
        commit = commits[0].get('sha') if isinstance(commits, list) and commits else None
        if not isinstance(commit, str) or not re.fullmatch(r'[0-9a-f]{40}', commit):
            raise ValueError('Invalid GitHub commit SHA')
        archive = self._get(f'https://codeload.github.com/{name}/tar.gz/{commit}', limit=20 * 1024 * 1024)
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=False)
        entries, expanded, kept_bytes = 0, 0, 0
        files, skipped, top = [], [], None
        try:
            tar = tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz')
        except tarfile.TarError as error:
            raise ValueError('Invalid GitHub source archive') from error
        with tar:
            for member in tar:
                entries += 1
                expanded += member.size
                if entries > 4000 or expanded > 40 * 1024 * 1024:
                    raise ValueError('Repository archive exceeds expanded/entry budget')
                path = PurePosixPath(member.name)
                if path.is_absolute() or '..' in path.parts or '\\' in member.name or not path.parts:
                    raise ValueError('Unsafe repository archive path')
                top = top or path.parts[0]
                if path.parts[0] != top:
                    raise ValueError('Repository archive must have one root')
                if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                    raise ValueError('Repository archive contains links or special files')
                if not member.isfile() or len(path.parts) < 2:
                    continue
                relative = PurePosixPath(*path.parts[1:])
                basename = relative.name.lower()
                retain = (relative.suffix == '.py' or basename.startswith(('readme', 'license', 'copying'))
                          or basename in {'pyproject.toml', 'requirements.txt'})
                if (not retain or any(p.startswith('.') or p in {'vendor', 'node_modules', 'venv', 'build', 'dist'}
                                     for p in relative.parts[:-1]) or member.size > 262144):
                    skipped.append(str(relative))
                    continue
                if len(files) >= 200 or kept_bytes + member.size > 5 * 1024 * 1024:
                    raise ValueError('Repository source snapshot exceeds file/byte budget')
                stream = tar.extractfile(member)
                if stream is None:
                    raise ValueError('Cannot read source archive member')
                content = stream.read(member.size + 1)
                if len(content) != member.size:
                    raise ValueError('Repository archive member length mismatch')
                target = root.joinpath(*relative.parts)
                if target.exists():
                    raise ValueError('Duplicate repository archive path')
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                kept_bytes += len(content)
                files.append({'path': str(relative), 'sha256': hashlib.sha256(content).hexdigest(),
                              'bytes': len(content)})
        license_files = [item['path'] for item in files if len(PurePosixPath(item['path']).parts) == 1
                         and PurePosixPath(item['path']).name.lower().startswith(('license', 'copying'))]
        if not files or not license_files:
            raise ValueError('Repository snapshot lacks source or a root license document')
        snapshot = {'repository': name, 'url': 'https://github.com/' + name, 'commit': commit,
                    'license': license_id, 'license_files': license_files, 'files': files,
                    'skipped_count': len(skipped), 'snapshot_kind': 'bounded_source_archive',
                    'archive_sha256': hashlib.sha256(archive).hexdigest(),
                    'download_trace': self.trace.copy(), 'executed_repository_code': False}
        (root / 'github-snapshot.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
        return snapshot
