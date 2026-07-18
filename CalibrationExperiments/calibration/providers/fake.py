from __future__ import annotations

import hashlib

from calibration.models import ProviderRequest, ProviderResponse
from calibration.providers.base import ModelProvider


class FakeProvider(ModelProvider):
    """Credential-free provider for exercising the complete harness."""

    name = "fake"
    max_concurrency = 20

    def __init__(self) -> None:
        self.call_count = 0

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        self.call_count += 1
        answer = request.messages[-1].content
        response_id = "fake-" + hashlib.sha256(
            f"{request.request_hash}:{answer}".encode("utf-8")
        ).hexdigest()[:16]
        return ProviderResponse(
            response_id=response_id,
            raw_response={"answer": answer, "model": request.dated_model_version},
            parsed_answer=answer,
            finish_reason="stop",
            input_tokens=sum(len(message.content.split()) for message in request.messages),
            output_tokens=len(answer.split()),
        )

