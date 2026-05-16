from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class GroupingStrategy(str, Enum):
    """허용된 그룹화 전략 집합."""

    TIME_BASED = "TIME_BASED"
    LOCATION_BASED = "LOCATION_BASED"
    SCENE_BASED = "SCENE_BASED"
    FOOD_TYPE_BASED = "FOOD_TYPE_BASED"
    STORY_FLOW_BASED = "STORY_FLOW_BASED"


@dataclass(frozen=True)
class SemanticScoreWeights:
    """의미 거리 평가에 쓰는 전략별 점수 가중치."""

    same_scene: float = 2.0
    same_location: float = 2.0
    shared_tag: float = 1.5
    two_or_more_shared_summary_words: float = 1.5
    one_shared_summary_word: float = 0.5
    conflicting_tags: float = -2.5

    def as_dict(self) -> dict[str, float]:
        """응답 근거에 남길 수 있는 dict로 변환한다."""

        return {
            "same_scene": self.same_scene,
            "same_location": self.same_location,
            "shared_tag": self.shared_tag,
            "two_or_more_shared_summary_words": self.two_or_more_shared_summary_words,
            "one_shared_summary_word": self.one_shared_summary_word,
            "conflicting_tags": self.conflicting_tags,
        }


@dataclass(frozen=True)
class GroupingStrategyProfile:
    """전략별 의미 태그 선택 규칙을 담는다."""

    strategy: GroupingStrategy
    allowed_tags: frozenset[str] | None = None
    semantic_score_weights: SemanticScoreWeights = SemanticScoreWeights()

    def filter_tags(self, tags: set[str]) -> set[str]:
        """이 전략이 비교에 사용할 태그만 남긴다."""

        if self.allowed_tags is None:
            return tags
        return {tag for tag in tags if tag in self.allowed_tags}


TAG_PATTERNS = {
    "beach": ["beach", "seaside", "coastal", "sand", "ocean", "sea", "sunset"],
    "urban": ["urban", "city", "street", "building", "night", "cityscape"],
    "portrait": ["person", "woman", "man", "people", "adult", "female"],
    "nature": ["sky", "cloud", "sunset", "water", "outdoor"],
    "ramen": ["ramen", "noodle", "broth"],
    "dessert": ["dessert", "cake", "cookie", "icecream", "ice", "sweet"],
    "coffee": ["coffee", "latte", "espresso", "cafe"],
    "meat": ["steak", "bbq", "barbecue", "grill", "meat"],
}

FOOD_TAGS = frozenset({"ramen", "dessert", "coffee", "meat"})
SCENE_TAGS = frozenset({"beach", "urban", "portrait", "nature"})

STRATEGY_PROFILES = {
    GroupingStrategy.TIME_BASED: GroupingStrategyProfile(GroupingStrategy.TIME_BASED),
    GroupingStrategy.LOCATION_BASED: GroupingStrategyProfile(GroupingStrategy.LOCATION_BASED),
    GroupingStrategy.SCENE_BASED: GroupingStrategyProfile(
        GroupingStrategy.SCENE_BASED,
        allowed_tags=SCENE_TAGS,
        semantic_score_weights=SemanticScoreWeights(
            same_scene=2.5,
            same_location=1.0,
            shared_tag=2.0,
        ),
    ),
    GroupingStrategy.FOOD_TYPE_BASED: GroupingStrategyProfile(
        GroupingStrategy.FOOD_TYPE_BASED,
        allowed_tags=FOOD_TAGS,
    ),
    GroupingStrategy.STORY_FLOW_BASED: GroupingStrategyProfile(
        GroupingStrategy.STORY_FLOW_BASED,
        semantic_score_weights=SemanticScoreWeights(
            same_scene=1.5,
            same_location=1.5,
            shared_tag=1.0,
            two_or_more_shared_summary_words=2.0,
            one_shared_summary_word=0.75,
        ),
    ),
}

CONFLICTING_TAG_PAIRS = [
    (frozenset({"beach"}), frozenset({"urban"})),
    (frozenset({"nature"}), frozenset({"urban"})),
]


def strategy_profile_for(strategy: GroupingStrategy) -> GroupingStrategyProfile:
    """전략에 해당하는 프로필을 반환한다."""

    return STRATEGY_PROFILES[strategy]
