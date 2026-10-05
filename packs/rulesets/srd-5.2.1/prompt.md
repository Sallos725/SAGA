# SRD 5.2.1 example

Selected system: SRD 5.2.1 example, version 0.2.0.
Use ability_check, saving_throw, or attack for rolls. Ability scores, proficiency, weapon data,
and AC come from stored actor stats. The GM supplies the check difficulty and circumstances in
validated input. Narrate the recorded resolution. Natural 1/20 overrides apply to attacks only.
Use damage or heal for GM-declared hit point changes; HP is clamped to 0..max and nothing happens
automatically at 0 HP. Use apply_condition or remove_condition to change named conditions
explicitly; conditions have no mechanical effect on rolls in this example. Use advance_time to
move simulated time in rounds, minutes, hours, or days.
Unsupported rules require a future implementation; never borrow Fate rules or invent their effects.

## Scope

This is a partial example implementation.

Implemented: ability checks; saving throws; one configured weapon attack; advantage/disadvantage; critical damage dice; HP subtraction; GM-declared damage and healing clamped to 0..max HP; explicit named conditions without exhaustion levels; simulated time in seconds with six-second rounds.

Not implemented: character creation and advancement; classes and spells; initiative, range, movement and action economy; resistances and vulnerabilities; death saves, instant death and automatic unconsciousness; condition effects on rolls and condition durations; inspiration and rules rerolls.

## Source and attribution

This work includes material from the System Reference Document 5.2.1 (“SRD 5.2.1”) by Wizards of the Coast LLC, available at https://www.dndbeyond.com/srd. The SRD 5.2.1 is licensed under the Creative Commons Attribution 4.0 International License, available at https://creativecommons.org/licenses/by/4.0/legalcode.

Source: https://media.dndbeyond.com/compendium-images/srd/5.2/SRD_CC_v5.2.1.pdf

License: CC-BY-4.0 — https://creativecommons.org/licenses/by/4.0/

Changes: Selected mechanics reimplemented in Python; original example actors and prompts. No source art, setting, or full rulebook is bundled.
