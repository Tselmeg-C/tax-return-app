import re

import pytest

from app.domain.enums import ALL_ENUMS, CATEGORY_GROUP, LABELS_DE, Category, CategoryGroup


def test_category_has_30_values_all_grouped() -> None:
    assert len(Category) == 30
    assert set(CATEGORY_GROUP) == set(Category)
    assert all(isinstance(group, CategoryGroup) for group in CATEGORY_GROUP.values())
    assert set(CATEGORY_GROUP.values()) == set(CategoryGroup)  # every group is used


@pytest.mark.parametrize("enum_cls", ALL_ENUMS, ids=lambda e: e.__name__)
def test_values_are_lowercase_ascii_codes(enum_cls: type) -> None:
    values = [member.value for member in enum_cls]
    assert values
    assert len(values) == len(set(values))
    for value in values:
        assert re.fullmatch(r"[a-z0-9_]+", value), value


@pytest.mark.parametrize("enum_cls", list(LABELS_DE), ids=lambda e: e.__name__)
def test_labels_cover_every_member(enum_cls: type) -> None:
    labels = LABELS_DE[enum_cls]
    assert set(labels) == set(enum_cls)
    assert all(type(member) is enum_cls for member in labels)
    assert all(label.strip() for label in labels.values())
