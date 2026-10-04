"""Allowlisted, system-sized modules. No downloaded code or prompt-controlled imports."""

from collections.abc import Callable
from dataclasses import dataclass

from nmos_trpg.domain.models import ActorState, WorldState
from nmos_trpg.domain.rulesets import RulesetManifest
from nmos_trpg.modules.base import RuleModule
from nmos_trpg.modules.fate_condensed.rules import FateCondensedRules
from nmos_trpg.modules.fate_condensed.rules import example_world as fate_world
from nmos_trpg.modules.generic_fantasy.rules import GenericFantasyRules
from nmos_trpg.modules.srd_5_2_1.rules import SRD521Rules
from nmos_trpg.modules.srd_5_2_1.rules import example_world as srd_world

COMMON_BOT_DESCRIPTION = """You are the game master of this campaign.
Describe scenes and portray non-player characters; leave player decisions to the player.
Use only the campaign's selected ruleset and its declared supported actions.
Propose structured actions using the selected module's event schemas.
Use committed runtime facts, state, rolls, and resolution as authoritative.
Never invent rolls, change exact state through prose, or resolve an unsupported mechanic silently.
When pending_adjudication is true, explain the unresolved choice; do not claim its cost was applied.
Regenerating narration reuses the same committed event and never dispatches a new action.
"""

SRD_ATTRIBUTION = (
    "This work includes material from the System Reference Document 5.2.1 (“SRD 5.2.1”) "
    "by Wizards of the Coast LLC, available at https://www.dndbeyond.com/srd. "
    "The SRD 5.2.1 is licensed under the Creative Commons Attribution 4.0 International License, "
    "available at https://creativecommons.org/licenses/by/4.0/legalcode."
)
FATE_ATTRIBUTION = (
    "Fate Condensed ©2020 Evil Hat Productions, LLC.\n\n"
    "This work is based on Fate Condensed (found at http://www.faterpg.com/), "
    "a product of Evil Hat Productions, LLC, developed, authored, and edited by PK Sullivan, "
    "Lara Turner, Leonard Balsera, Fred Hicks, Richard Bellingham, Robert Hanz, Ryan Macklin, "
    "and Sophie Lagacé, and licensed for "
    "our use under the Creative Commons Attribution 3.0 Unported license "
    "(http://creativecommons.org/licenses/by/3.0/)."
)


@dataclass(frozen=True)
class RulesetDefinition:
    rules: RuleModule
    manifest: RulesetManifest
    example_world: Callable[[str], WorldState]


def legacy_world(world_id: str) -> WorldState:
    return WorldState(
        world_id=world_id,
        actors={
            "player": ActorState(actor_id="player", hp=20, max_hp=20),
            "goblin": ActorState(actor_id="goblin", hp=12, max_hp=12),
        },
    )


class RulesetRegistry:
    def __init__(self) -> None:
        srd = SRD521Rules()
        fate = FateCondensedRules()
        legacy = GenericFantasyRules()
        definitions = [
            RulesetDefinition(
                srd,
                RulesetManifest(
                    ruleset_id=srd.name,
                    ruleset_version=srd.version,
                    title="SRD 5.2.1 example",
                    implementation_scope="example",
                    source_url="https://media.dndbeyond.com/compendium-images/srd/5.2/SRD_CC_v5.2.1.pdf",
                    source_revision="SRD 5.2.1, pages 6–8 and 16; checked 2026-10-04",
                    license_id="CC-BY-4.0",
                    license_url="https://creativecommons.org/licenses/by/4.0/",
                    attribution=SRD_ATTRIBUTION,
                    modifications=(
                        "Selected mechanics reimplemented in Python; original example actors "
                        "and prompts. No source art, setting, or full rulebook is bundled."
                    ),
                    implemented=[
                        "ability checks",
                        "saving throws",
                        "one configured weapon attack",
                        "advantage/disadvantage",
                        "critical damage dice",
                        "HP subtraction",
                    ],
                    not_implemented=[
                        "character creation and advancement",
                        "classes and spells",
                        "initiative, range, movement and action economy",
                        "conditions and resistances",
                        "death saves, instant death and unconsciousness",
                        "inspiration and rules rerolls",
                    ],
                    narrator_instructions="""Selected system: SRD 5.2.1 example, version 0.1.0.
Use ability_check, saving_throw, or attack. Ability scores, proficiency, weapon data, and AC come
from stored actor stats. The GM supplies the check difficulty and circumstances in validated input.
Narrate the recorded resolution. Natural 1/20 overrides apply to attacks only.
HP reaching zero is recorded; this example does not resolve death or conditions.
Unsupported rules require a future implementation; never borrow Fate rules or invent their effects.
""",
                    event_payload_schemas={
                        kind: model.model_json_schema()
                        for kind, model in srd.payload_models.items()
                    },
                ),
                srd_world,
            ),
            RulesetDefinition(
                fate,
                RulesetManifest(
                    ruleset_id=fate.name,
                    ruleset_version=fate.version,
                    title="Fate Condensed example",
                    implementation_scope="example",
                    source_url="https://fate-srd.com/downloads/CC-BY-SRDs.zip",
                    source_revision=(
                        "Fate-Condensed-SRD-CC-BY.html; SHA256 "
                        "a0f556ce2415fe4dc88ff71e56755bb92e113a52f8041bda69b759b63782544e; "
                        "checked 2026-10-04"
                    ),
                    license_id="CC-BY-3.0",
                    license_url="https://creativecommons.org/licenses/by/3.0/",
                    attribution=FATE_ATTRIBUTION,
                    modifications=(
                        "Selected unmodified-roll mechanics reimplemented in Python; "
                        "4d3-8 represents four Fate dice. Original example actors and prompts. "
                        "Costs remain explicit pending adjudications."
                    ),
                    implemented=[
                        "overcome against static opposition",
                        "four outcome grades",
                        "create a new situation aspect",
                        "one/two free invokes on new aspects",
                        "boost records",
                    ],
                    not_implemented=[
                        "character creation and advancement",
                        "paid/free invocations and rerolls",
                        "stunts and compels",
                        "attack, defend, stress and consequences",
                        "opposed rolls and existing-aspect advantages",
                        "applying costs and boost expiry",
                    ],
                    narrator_instructions="""Selected system: Fate Condensed example, version 0.1.0.
Use overcome or create_advantage against static opposition, with a stored skill:<name> rating.
Four journaled d3 faces map 1/2/3 to -1/0/+1. The recorded total includes the skill rating.
An overcome failure/tie, or failed advantage, leaves a fictional choice pending; do not state that
a cost or adverse aspect was applied. Successful new aspects and boosts are stored in world flags.
Free invokes are recorded but spending invokes and expiring boosts are not automated in this slice.
Do not use generic HP or SRD combat mechanics to fill in unsupported Fate rules.
""",
                    event_payload_schemas={
                        kind: model.model_json_schema()
                        for kind, model in fate.payload_models.items()
                    },
                ),
                fate_world,
            ),
            RulesetDefinition(
                legacy,
                RulesetManifest(
                    ruleset_id=legacy.name,
                    ruleset_version=legacy.version,
                    title="Legacy generic fantasy scaffold",
                    implementation_scope="legacy-scaffold",
                    modifications="Existing local demonstration rules.",
                    implemented=["legacy attack", "set_flag", "time_advance"],
                    not_implemented=[
                        "closed rule payload validation",
                        "complete published game system",
                    ],
                    narrator_instructions=(
                        "Use the existing generic-fantasy scaffold contract; "
                        "this is not a published ruleset adaptation."
                    ),
                ),
                legacy_world,
            ),
        ]
        self._definitions = {(item.rules.name, item.rules.version): item for item in definitions}

    def get(self, ruleset_id: str, version: str) -> RulesetDefinition:
        try:
            return self._definitions[(ruleset_id, version)]
        except KeyError as exc:
            raise ValueError(f"unsupported ruleset/version: {ruleset_id}@{version}") from exc

    def manifests(self) -> list[RulesetManifest]:
        return [item.manifest.model_copy(deep=True) for item in self._definitions.values()]
