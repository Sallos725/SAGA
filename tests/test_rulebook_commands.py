import pytest

from saga_trpg.rulebooks.commands import CommandError, command_help, parse_command
from saga_trpg.rulebooks.loader import load_rulebook, rulebook_path

SRD = load_rulebook(rulebook_path("srd-5.2.1"))
FATE = load_rulebook(rulebook_path("fate-condensed"))


@pytest.mark.parametrize(
    "text,kind,payload",
    [
        (
            "attack opponent",
            "attack",
            {"target_id": "opponent", "advantage": False, "disadvantage": False},
        ),
        (
            "Attack  OPPONENT advantage",
            "attack",
            {"target_id": "opponent", "advantage": True, "disadvantage": False},
        ),
        (
            "check strength 10",
            "ability_check",
            {
                "ability": "strength",
                "difficulty": 10,
                "proficiency": None,
                "advantage": False,
                "disadvantage": False,
            },
        ),
        (
            "CHECK strength 010 disadvantage",
            "ability_check",
            {
                "ability": "strength",
                "difficulty": 10,
                "proficiency": None,
                "advantage": False,
                "disadvantage": True,
            },
        ),
        (
            "ability_check strength 15 proficiency=athletics",
            "ability_check",
            {
                "ability": "strength",
                "difficulty": 15,
                "proficiency": "athletics",
                "advantage": False,
                "disadvantage": False,
            },
        ),
        (
            "save dexterity 12",
            "saving_throw",
            {"ability": "dexterity", "difficulty": 12, "advantage": False, "disadvantage": False},
        ),
        ("damage opponent 5", "damage", {"target_id": "opponent", "amount": 5}),
        ("heal player 3", "heal", {"target_id": "player", "amount": 3}),
        (
            "inflict opponent prone",
            "apply_condition",
            {"target_id": "opponent", "condition": "prone"},
        ),
        (
            "cure opponent prone",
            "remove_condition",
            {"target_id": "opponent", "condition": "prone"},
        ),
        ("wait 2 rounds", "advance_time", {"amount": 2, "unit": "round"}),
        ("advance_time 1 hour", "advance_time", {"amount": 1, "unit": "hour"}),
        ("give player torch 3", "give", {"target_id": "player", "item": "torch", "amount": 3}),
        ("take player torch 1", "take", {"target_id": "player", "item": "torch", "amount": 1}),
        ("damage target_id=opponent amount=7", "damage", {"target_id": "opponent", "amount": 7}),
    ],
)
def test_srd_commands_keep_their_shapes(text, kind, payload):
    event = parse_command(SRD, actor_id="player", text=text)
    assert (event.kind, event.payload) == (kind, payload)
    assert event.actor_id == "player"


@pytest.mark.parametrize(
    "text,kind,payload",
    [
        (
            "overcome investigate 2",
            "overcome",
            {"skill": "investigate", "difficulty": 2, "boost_name": "Temporary advantage"},
        ),
        (
            "overcome athletics -1 boost_name=Opening",
            "overcome",
            {"skill": "athletics", "difficulty": -1, "boost_name": "Opening"},
        ),
        (
            'create_advantage notice 1 aspect_name="Mapped route"',
            "create_advantage",
            {"skill": "notice", "difficulty": 1, "aspect_name": "Mapped route"},
        ),
        (
            "create_advantage notice 1 'Mapped route'",
            "create_advantage",
            {"skill": "notice", "difficulty": 1, "aspect_name": "Mapped route"},
        ),
    ],
)
def test_fate_commands_come_from_the_rulebook(text, kind, payload):
    event = parse_command(FATE, actor_id="player", text=text)
    assert (event.kind, event.payload) == (kind, payload)


@pytest.mark.parametrize(
    "spec,text,message",
    [
        (SRD, "", "empty"),
        (SRD, "dance opponent", "unknown action"),
        (SRD, "attack", "target_id"),
        (SRD, "attack opponent goblin", "too many"),
        (SRD, "attack opponent advantage advantage", "twice"),
        (SRD, "check strength ten", "difficulty"),
        (SRD, "check strength 10 colour=red", "colour"),
        (SRD, "damage opponent 5 advantage", "advantage"),
        (SRD, "attack opponent=1", "no input"),
        (SRD, 'attack "opponent', "quote"),
        (FATE, "attack opponent", "unknown action"),
        (FATE, "overcome investigate", "difficulty"),
        (FATE, "create_advantage notice 1", "aspect_name"),
    ],
)
def test_malformed_commands_fail_with_the_field_or_reason(spec, text, message):
    with pytest.raises(CommandError, match=message):
        parse_command(spec, actor_id="player", text=text)


def test_parsing_is_a_pure_identity_function():
    a = parse_command(SRD, actor_id="player", text="  attack   Opponent ")
    b = parse_command(SRD, actor_id="player", text="ATTACK opponent")
    assert a == b


def test_help_lists_every_action_with_its_positional_inputs():
    text = command_help(FATE)
    assert "overcome <skill> <difficulty> [boost_name=…]" in text
    assert "create_advantage <skill> <difficulty> <aspect_name>" in text
    srd = command_help(SRD)
    assert "attack <target_id> [advantage|disadvantage]" in srd
    assert "(alias: check)" in srd and "advance_time <amount> <unit>  (alias: wait)" in srd
