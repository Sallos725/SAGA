import json

import pytest

from saga_trpg.domain.models import ActorState, WorldState
from saga_trpg.rulebooks.display import render_display
from saga_trpg.rulebooks.loader import RulebookError, load_rulebook, parse_rulebook, rulebook_path
from tests.test_rulebooks import write_spec

SRD = load_rulebook(rulebook_path("srd-5.2.1"))
FATE = load_rulebook(rulebook_path("fate-condensed"))


def test_bundled_rulebooks_declare_what_the_footer_shows():
    assert SRD.display.actor == ["hp", "conditions", "inventory"]
    assert SRD.display.world == ["time"]
    assert FATE.display.actor == ["skill:*"]
    assert FATE.display.records == ["fate_aspects", "fate_boosts"]
    assert FATE.display.world == []


def test_srd_display_lines_follow_the_declaration():
    state = WorldState(
        world_id="w",
        simulated_time=3725,
        actors={
            "player": ActorState(
                actor_id="player", hp=13, max_hp=20, inventory={"torch": 3, "silver_key": 1}
            ),
            "opponent": ActorState(
                actor_id="opponent", hp=0, max_hp=30, conditions={"prone", "unconscious"}
            ),
        },
    )
    display = render_display(SRD, state)
    assert display == {
        "actors": [
            {"id": "player", "items": ["13/20", "🎒 silver_key×1, torch×3"]},
            {"id": "opponent", "items": ["0/30", "prone, unconscious"]},
        ],
        "records": [],
        "world": ["⏱ 1h 2m 5s"],
    }


def test_fate_display_shows_skills_and_records_not_hp():
    state = WorldState(
        world_id="w",
        actors={
            "player": ActorState(
                actor_id="player",
                stats={"skill:investigate": 2, "skill:athletics": 1, "skill:notice": 3},
            )
        },
        flags={
            "fate_aspects": {
                "e1": {"name": "Mapped route", "owner_id": "player", "free_invokes": 2}
            },
            "fate_boosts": {},
        },
    )
    display = render_display(FATE, state)
    assert display["actors"] == [
        {"id": "player", "items": ["athletics +1, investigate +2, notice +3"]}
    ]
    assert display["records"] == [
        {"label": "fate_aspects", "items": ["Mapped route (owner_id player, free_invokes 2)"]}
    ]
    assert display["world"] == []
    assert "10/10" not in json.dumps(display)


def test_display_defaults_when_a_rulebook_declares_nothing(tmp_path):
    data = json.loads(rulebook_path("srd-5.2.1").read_text())
    del data["display"]
    spec = load_rulebook(write_spec(tmp_path, data))
    assert spec.display.actor == ["hp", "conditions", "inventory"]
    assert spec.display.world == ["time"] and spec.display.records == []


@pytest.mark.parametrize(
    "mutate,message",
    [
        (lambda d: d["display"].update(actor=["mana"]), "display.actor"),
        (lambda d: d["display"].update(records=["ghosts"]), "display.records"),
        (lambda d: d["display"].update(world=["weather"]), "display.world"),
        (lambda d: d["display"].update(actor=["hp", "hp"]), "twice"),
    ],
)
def test_display_entries_are_validated(tmp_path, mutate, message):
    data = json.loads(rulebook_path("srd-5.2.1").read_text())
    mutate(data)
    with pytest.raises(RulebookError, match=message):
        parse_rulebook(data)


def test_stats_and_missing_values_render_gracefully():
    data = json.loads(rulebook_path("srd-5.2.1").read_text())
    data["display"]["actor"] = ["hp", "armor_class", "weapon_damage", "proficient:*"]
    spec = parse_rulebook(data)
    state = WorldState(
        world_id="w",
        actors={
            "player": ActorState(
                actor_id="player",
                stats={
                    "armor_class": 15,
                    "proficient:athletics": True,
                    "proficient:stealth": False,
                },
            )
        },
    )
    assert render_display(spec, state)["actors"] == [
        {"id": "player", "items": ["10/10", "armor_class 15", "athletics"]}
    ]


def test_display_falls_back_to_a_declared_stat_default():
    data = json.loads(rulebook_path("srd-5.2.1").read_text())
    data["stats"]["mana"] = {"type": "integer", "range": [0, 99], "default": 5}
    data["display"]["actor"] = ["mana", "armor_class"]
    spec = parse_rulebook(data)
    state = WorldState(world_id="w", actors={"player": ActorState(actor_id="player")})
    assert render_display(spec, state)["actors"] == [{"id": "player", "items": ["mana 5"]}]
