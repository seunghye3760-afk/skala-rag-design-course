"""오픈소스 임베딩 모델과 모델별 입력 규칙. 담당 1.

설계서 B-4: 후보 3종을 같은 청크·같은 질문으로 비교할 때 각 모델의 권장 접두어·instruction을 적용한다.
- bge-m3: 접두어 없음
- multilingual-e5-large: 질의 "query: ", 문서 "passage: " (필수)
- Qwen3-Embedding-0.6B: 질의에만 모델 내장 "query" 프롬프트(instruction)
모든 벡터는 정규화 → FAISS 내적 = 코사인 유사도.
"""
from __future__ import annotations

import os
from functools import lru_cache

MODELS: dict[str, dict] = {
    "BAAI/bge-m3": {"doc": {}, "query": {}},
    "intfloat/multilingual-e5-large": {"doc": {"prompt": "passage: "}, "query": {"prompt": "query: "}},
    "Qwen/Qwen3-Embedding-0.6B": {"doc": {}, "query": {"prompt_name": "query"}},
}


def slug(model_name: str) -> str:
    """인덱스 폴더 이름. 'BAAI/bge-m3' → 'bge-m3'"""
    return model_name.split("/")[-1]


def _device(default: str) -> str:
    # runtime.yaml은 팀 공통(cpu). 개인 PC에서 mps/cuda를 쓰려면 환경변수로만 바꾼다.
    return os.getenv("KV_EMBED_DEVICE", default)


@lru_cache(maxsize=2)
def _model(model_name: str, device: str):
    from sentence_transformers import SentenceTransformer  # 무거움: 함수 안 import

    if model_name not in MODELS:
        raise KeyError(f"등록되지 않은 임베딩 모델: {model_name} (rag/embeddings.py MODELS)")
    return SentenceTransformer(model_name, device=device)


def encode_docs(texts: list[str], model_name: str, device: str = "cpu"):
    m = _model(model_name, _device(device))
    return m.encode(texts, batch_size=16, normalize_embeddings=True, convert_to_numpy=True,
                    show_progress_bar=len(texts) > 50, **MODELS[model_name]["doc"]).astype("float32")


def encode_query(text: str, model_name: str, device: str = "cpu"):
    m = _model(model_name, _device(device))
    return m.encode([text], normalize_embeddings=True, convert_to_numpy=True,
                    **MODELS[model_name]["query"]).astype("float32")
