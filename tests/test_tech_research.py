"""기술 조사 LLM 출력 길이 제한과 복구 동작."""
from types import SimpleNamespace

from openai import LengthFinishReasonError

from kv_eval.agents.tech_research import LENGTH_RETRY_TOP_K, BriefAnswer, _invoke_extract
from kv_eval.graph.task_schema import Chunk, Locator


def _length_error():
    return LengthFinishReasonError(completion=SimpleNamespace(usage="test"))


def _chunks(n=4):
    return [
        Chunk(chunk_id=f"c{i}", text=f"text-{i}", locator=Locator(doc_id="turboquant", page=i))
        for i in range(n)
    ]


def _format(chunks):
    return ",".join(c.chunk_id for c in chunks)


class _QueueExtract:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.payloads = []

    def invoke(self, payload):
        self.payloads.append(payload.copy())
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_length_error_retries_once_with_smaller_context():
    answer = BriefAnswer(relevant=True, status="reported", value="요약", chunk_ids=["c0"])
    extract = _QueueExtract(_length_error(), answer)

    result = _invoke_extract(extract, "TurboQuant", "질문", _chunks(), _format)

    assert result == answer
    assert extract.payloads[0]["context"] == "c0,c1,c2,c3"
    assert extract.payloads[1]["context"] == ",".join(f"c{i}" for i in range(LENGTH_RETRY_TOP_K))


def test_second_length_error_returns_none_instead_of_crashing():
    extract = _QueueExtract(_length_error(), _length_error())

    result = _invoke_extract(extract, "TurboQuant", "질문", _chunks(), _format)

    assert result is None
    assert len(extract.payloads) == 2
