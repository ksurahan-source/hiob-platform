from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Result:
    data: Any


class Query:
    def __init__(self, client: "QueueClient", table: str):
        self.client = client
        self.table_name = table
        self.operation = "select"
        self.payload: Any = None
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def _chain(self, name: str, *args: Any, **kwargs: Any) -> "Query":
        self.calls.append((name, args, kwargs))
        return self

    def select(self, *args: Any, **kwargs: Any) -> "Query":
        return self._chain("select", *args, **kwargs)

    def eq(self, *args: Any, **kwargs: Any) -> "Query":
        return self._chain("eq", *args, **kwargs)

    def neq(self, *args: Any, **kwargs: Any) -> "Query":
        return self._chain("neq", *args, **kwargs)

    def in_(self, *args: Any, **kwargs: Any) -> "Query":
        return self._chain("in_", *args, **kwargs)

    def order(self, *args: Any, **kwargs: Any) -> "Query":
        return self._chain("order", *args, **kwargs)

    def limit(self, *args: Any, **kwargs: Any) -> "Query":
        return self._chain("limit", *args, **kwargs)

    def single(self, *args: Any, **kwargs: Any) -> "Query":
        return self._chain("single", *args, **kwargs)

    def maybe_single(self, *args: Any, **kwargs: Any) -> "Query":
        return self._chain("maybe_single", *args, **kwargs)

    def is_(self, *args: Any, **kwargs: Any) -> "Query":
        return self._chain("is_", *args, **kwargs)

    def contains(self, *args: Any, **kwargs: Any) -> "Query":
        return self._chain("contains", *args, **kwargs)

    def filter(self, *args: Any, **kwargs: Any) -> "Query":
        return self._chain("filter", *args, **kwargs)

    @property
    def not_(self) -> "Query":
        return self._chain("not_")

    def insert(self, payload: Any, *args: Any, **kwargs: Any) -> "Query":
        self.operation = "insert"
        self.payload = payload
        return self._chain("insert", payload, *args, **kwargs)

    def update(self, payload: Any, *args: Any, **kwargs: Any) -> "Query":
        self.operation = "update"
        self.payload = payload
        return self._chain("update", payload, *args, **kwargs)

    def upsert(self, payload: Any, *args: Any, **kwargs: Any) -> "Query":
        self.operation = "upsert"
        self.payload = payload
        return self._chain("upsert", payload, *args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> "Query":
        self.operation = "delete"
        return self._chain("delete", *args, **kwargs)

    def execute(self) -> Result:
        self.client.executed.append(self)
        return Result(self.client.pop(self.table_name, self.operation))


class QueueClient:
    def __init__(self):
        self.responses: dict[tuple[str, str], list[Any]] = {}
        self.executed: list[Query] = []
        self.rpc_responses: dict[str, list[Any]] = {}
        self.rpc_calls: list[tuple[str, dict[str, Any]]] = []

    def queue(self, table: str, data: Any, operation: str = "select") -> None:
        self.responses.setdefault((table, operation), []).append(data)

    def pop(self, table: str, operation: str) -> Any:
        queued = self.responses.get((table, operation), [])
        value = queued.pop(0) if queued else []
        if isinstance(value, BaseException):
            raise value
        return value

    def table(self, name: str) -> Query:
        return Query(self, name)

    def queue_rpc(self, name: str, data: Any) -> None:
        self.rpc_responses.setdefault(name, []).append(data)

    def rpc(self, name: str, params: dict[str, Any]) -> "RpcQuery":
        self.rpc_calls.append((name, params))
        return RpcQuery(self, name)


class RpcQuery:
    def __init__(self, client: QueueClient, name: str):
        self.client = client
        self.name = name

    def execute(self) -> Result:
        queued = self.client.rpc_responses.get(self.name, [])
        value = queued.pop(0) if queued else None
        if isinstance(value, BaseException):
            raise value
        return Result(value)
