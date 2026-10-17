"""Small framework-independent tool client; no business-system wiring required."""

import time

import httpx


class ComponentClient:
    def __init__(self, base_url, token, transport=None):
        self.http = httpx.Client(base_url=base_url.rstrip('/'), headers={'Authorization': 'Bearer ' + token},
                                 timeout=20, transport=transport)

    def close(self):
        self.http.close()

    def catalog(self):
        response = self.http.get('/v1/components')
        response.raise_for_status()
        return response.json()

    def submit(self, request, *, plan_only=False):
        response = self.http.post('/v1/components/' + ('plan' if plan_only else 'ensure'), json=request)
        response.raise_for_status()
        return response.json()

    def invoke(self, slot, payload, *, contract_version, expected_revision=None):
        response = self.http.post('/v1/components/slots/' + slot + '/invoke',
                                  json={'input': payload, 'contract_version': contract_version,
                                        'expected_revision': expected_revision})
        response.raise_for_status()
        return response.json()

    def wait(self, job_id, timeout=600, poll_interval=1):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            response = self.http.get('/v1/jobs/' + job_id)
            response.raise_for_status()
            value = response.json()
            if value['status'] == 'failed':
                raise RuntimeError(value.get('error') or 'Component job failed')
            if value['status'] == 'succeeded':
                return value['result']
            time.sleep(min(poll_interval, max(0, deadline - time.monotonic())))
        raise TimeoutError('Component job remains pending; query its job_id to continue observing it')
