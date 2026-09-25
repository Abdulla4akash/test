"""A scripted, offline network for the demo (and a model for the tests).

It stands in for the transport, the DNS resolver and the timer, so a demo
collection goes through the real HTTP layer (robots.txt, rate limits,
budgets, logging) without leaving the machine. An unscripted URL raises.
"""

import json
from dataclasses import dataclass, field

from .fetch import RawResponse


@dataclass
class OfflineNet:
    responses: dict = field(default_factory=dict)
    sent: list[str] = field(default_factory=list)
    now: float = 1000.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def resolve(self, host: str, port: int) -> list[str]:
        return ["93.184.216.34"]  # a public documentation address; never contacted

    def send(self, request) -> RawResponse:
        self.sent.append(request.url)
        self.now += 0.05
        if request.url in self.responses:
            return self.responses[request.url]
        if request.url.endswith("/robots.txt"):
            return RawResponse(404, {"content-type": "text/plain"}, b"")
        raise AssertionError(f"offline network: unscripted request {request.url}")

    def json(self, url: str, payload, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.responses[url] = RawResponse(status, {"content-type": "application/json"}, body)

    def status(self, url: str, status: int) -> None:
        self.responses[url] = RawResponse(status, {"content-type": "text/plain"}, b"error")
