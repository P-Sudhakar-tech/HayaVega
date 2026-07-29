from dataclasses import dataclass
import json


@dataclass(frozen=True)
class Response:
    body: str
    status: int = 200
    content_type: str = "text/html; charset=utf-8"
    headers: tuple[tuple[str, str], ...] = ()


def json_response(data, status: int = 200, headers: tuple[tuple[str, str], ...] = ()) -> Response:
    return Response(
        json.dumps(data, separators=(",", ":")),
        status=status,
        content_type="application/json; charset=utf-8",
        headers=headers,
    )
