import pytest

from saga_trpg.rulebooks.expr import ExpressionError, compile_expression


def evaluate(text, **scope):
    return compile_expression(text).evaluate(scope)


@pytest.mark.parametrize(
    "text,scope,expected",
    [
        ("1 + 2 * 3", {}, 7),
        ("(score - 10) // 2", {"score": 7}, -2),
        ("(score - 10) // 2", {"score": 15}, 2),
        ("total >= target", {"total": 12, "target": 12}, True),
        ("die == 20 or total >= target", {"die": 3, "total": 1, "target": 9}, False),
        ("not proficient", {"proficient": False}, True),
        ("bonus if proficient else 0", {"proficient": True, "bonus": 2}, 2),
        ("max(10, damage // 2)", {"damage": 30}, 15),
        ("min(a, b) + abs(-3)", {"a": 4, "b": 9}, 7),
        ("stats.strength", {"stats": {"strength": 16}}, 16),
        ("stats[ability]", {"stats": {"strength": 16}, "ability": "strength"}, 16),
        ('ability == "strength"', {"ability": "strength"}, True),
        ("target.armor_class", {"target": {"armor_class": 13}}, 13),
        ("7 % 3", {}, 1),
        ("-score", {"score": 4}, -4),
    ],
)
def test_expressions_evaluate_deterministically(text, scope, expected):
    assert evaluate(text, **scope) == expected


@pytest.mark.parametrize(
    "text",
    [
        "__import__('os')",
        "stats.__class__",
        "open('x')",
        "a / b",
        "1.5 + 1",
        "[1, 2]",
        "{'a': 1}",
        "lambda x: x",
        "x = 1",
        "f(1)",
        "a ** 2",
        "a if b",
        "a.b(",
        "1e400",
        "x" * 600 + " + 1",
    ],
)
def test_disallowed_syntax_is_rejected_at_compile_time(text):
    with pytest.raises(ExpressionError):
        compile_expression(text)


def test_unknown_names_and_keys_fail_at_evaluation_with_the_name():
    expression = compile_expression("stats[ability] + bonus")
    with pytest.raises(ExpressionError, match="bonus"):
        expression.evaluate({"stats": {"strength": 1}, "ability": "strength"})
    with pytest.raises(ExpressionError, match="dexterity"):
        expression.evaluate({"stats": {"strength": 1}, "ability": "dexterity", "bonus": 0})


def test_only_integers_booleans_and_strings_flow_through():
    with pytest.raises(ExpressionError):
        evaluate("a + 1", a=1.5)
    with pytest.raises(ExpressionError):
        evaluate('a + "x"', a=1)
    with pytest.raises(ExpressionError, match="too large"):
        evaluate("a * a", a=10**40)


def test_expression_names_are_reported_for_validation():
    assert compile_expression("stats[ability].modifier + (bonus if proficient else 0)").names == {
        "stats",
        "ability",
        "bonus",
        "proficient",
    }
