"""신뢰하는 로컬 State 모델만 허용하는 LangGraph 체크포인트 직렬화 설정."""
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

_MODELS = (
    "TaskPlan", "WorkItem", "Evidence", "Locator", "EvidenceBrief", "CriterionResult",
    "RetryTarget", "WorkerResult", "SupervisorDecision", "TRLResult", "ConflictCandidate",
    "QualityEvaluation", "QualityDimension",
)


def checkpoint_serializer() -> JsonPlusSerializer:
    module = "kv_eval.graph.task_schema"
    return JsonPlusSerializer(allowed_msgpack_modules=[(module, name) for name in _MODELS])
