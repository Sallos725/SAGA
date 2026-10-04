from saga_trpg.runtime.rng import DeterministicDice


def test_deterministic_dice_keying() -> None:
    dice = DeterministicDice("bench-seed")
    kwargs = dict(world_id="w", event_id="e1", roll_key="attack", expression="1d20+5")
    assert dice.roll(**kwargs) == dice.roll(**kwargs)


def test_different_roll_keys_are_independent() -> None:
    dice = DeterministicDice("bench-seed")
    attack = dice.roll(world_id="w", event_id="e1", roll_key="attack", expression="1d20")
    damage = dice.roll(world_id="w", event_id="e1", roll_key="damage", expression="1d20")
    assert attack.roll_key != damage.roll_key
