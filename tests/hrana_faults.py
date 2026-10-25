"""One-shot response loss around real SQLite execution, with HTTP accounting."""

import json
from uuid import uuid4

import httpx

from test_remote_storage import HranaSQLite


class FaultInjectingHrana(HranaSQLite):
    def __init__(self, path):
        super().__init__(path, primary_constraint_codes=True)
        self.requests = []
        self.fault = None
        self.failures = 0

    def arm(self, sql_prefix, *, before=False):
        assert self.fault is None
        self.fault = (sql_prefix, before)

    def count_sql(self, prefix):
        return sum(
            op.get("stmt", {}).get("sql", "").lstrip().startswith(prefix)
            for body in self.requests
            for op in body["requests"]
        )

    def __call__(self, request):
        body = json.loads(request.content)
        self.requests.append(body)
        fault = self.fault
        should_fail = fault and any(
            op.get("stmt", {}).get("sql", "").lstrip().startswith(fault[0])
            for op in body["requests"]
        )
        if should_fail:
            self.fault = None
            self.failures += 1
        response = None if should_fail and fault[1] else super().__call__(request)
        if response is not None:
            payload = response.json()
            old_baton = payload["baton"]
            if old_baton is not None:
                # The v2 spec requires clients to use the latest baton, rather
                # than treating it as a permanent connection ID.
                payload["baton"] = str(uuid4())
                self.streams[payload["baton"]] = self.streams.pop(old_baton)
                response = httpx.Response(200, json=payload)
        if should_fail:
            # Model a disconnected stream expiring/rolling back on the server.
            # A lost COMMIT response can already be durable; never undo it here.
            for baton in {
                body["baton"],
                response.json()["baton"] if response else None,
            }:
                connection = self.streams.pop(baton, None)
                if connection is not None:
                    connection.close()
            raise httpx.ReadTimeout(
                "private-body recovery-db-token Bearer do-not-log", request=request
            )
        return response
