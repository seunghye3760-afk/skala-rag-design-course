"""근거 등급(A~D)·pro/con·측정 유형 분류 (설계서 C-4). 담당 2.
출처 유형·측정 유형 판정 규칙은 코드로 고정하고, LLM은 보조로만 쓴다 (설계서 D-8 주석)."""
from __future__ import annotations

# 원출처 불명 2차 자료 → D
_SECONDARY = ("기사", "뉴스", "커뮤니티", "포럼", "sns", "소셜", "헤드라인", "요약",
              "news", "media", "forum", "reddit", "twitter")
# 공식 공시(실적·양산)는 작성 주체와 무관하게 A
_DISCLOSURE = ("공시", "사업보고서", "실적 보고", "ir ", "annual report", "10-k", "filing")
# 시뮬레이션·분석 모델·비용 모델 등 추정 기반 → C (작성 주체 무관)
_ESTIMATE = ("시뮬레이션", "추정", "모델", "전망", "예측", "계산",
             "simulation", "model", "estimate", "projection", "forecast", "tco")
# 실측·재현·벤치마크
_MEASURED = ("실측", "측정", "벤치마크", "재현", "실험",
             "measurement", "measured", "benchmark", "reproduction", "empirical")
# 의견·인터뷰·발언 (실측 아님)
_OPINION = ("의견", "인터뷰", "발언", "논평", "opinion", "interview", "commentary")
# 블로그는 기본 2차 자료. 공식·기술 블로그(당사자 1차 발표)만 예외
_OFFICIAL_BLOG = ("공식", "기술", "official", "engineering")


def _is_secondary(src: str) -> bool:
    if any(k in src for k in _SECONDARY):
        return True
    if ("블로그" in src or "blog" in src) and not any(k in src for k in _OFFICIAL_BLOG):
        return True
    return False


def grade(source_type: str, measurement_type: str, is_independent: bool,
          cited_primary_type: str | None = None) -> str:
    """A 1차·독립 실측(또는 공시) / B 1차·당사자 / C 시뮬레이션·추정 / D 2차 자료.

    - 2차 자료(원출처 불명 기사·커뮤니티·비공식 블로그)는 내용과 무관하게 D
    - 공식 공시(사업보고서·실적)는 A
    - 추정 기반(시뮬레이션·비용 모델·전망)은 작성 주체와 무관하게 C
    - 실측·재현은 독립 주체면 A, 개발 주체·이해당사자면 B
    - 의견·발언은 당사자면 B(공식 발표), 독립이면 C(추정 기반 분석)
    - 판정 불가(모르는 유형·조건 없는 수치)는 보수적으로 D

    cited_primary_type: 2차 자료가 특정 1차 출처(예: '논문', '공식 문서', '보도자료')를
    구체적으로 인용하고 있으면 그 유형 (막연한 "보도에 따르면"은 제외). 이 경우 2차
    자료라는 이유만으로 D를 매기지 않고 인용된 1차 출처 유형으로 재분류한다 (설계서 C-4
    "재분류"). LLM이 인용을 식별한 것을 근거로 하며, 인용된 원문을 별도로 fetch해서
    재확인하는 단계는 포함하지 않는다.
    """
    src = source_type.strip().lower()
    mt = measurement_type.strip().lower()
    if _is_secondary(src):
        if not cited_primary_type:
            return "D"
        src = cited_primary_type.strip().lower()   # 원출처 유형으로 재분류하고 계속 판정
    if any(k in src for k in _DISCLOSURE):
        return "A"
    if any(k in mt for k in _ESTIMATE):
        return "C"
    if any(k in mt for k in _MEASURED):
        return "A" if is_independent else "B"
    if any(k in mt for k in _OPINION):
        return "C" if is_independent else "B"
    if any(k in mt for k in ("발표", "공지", "문서", "announcement", "release", "documentation")):
        return "B"
    return "D"
