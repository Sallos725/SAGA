# Example ruleset sources and attribution

Sources checked on 2026-10-04. These examples use specifically identified, openly licensed
SRD material. This does not grant rights to every book, setting, illustration, logo, or other
product associated with a game name. No source artwork, logos, fonts, or complete rulebooks
are bundled. Preserve the source, attribution, license link, and modification notices when
redistributing these examples. This notice does not relicense unrelated repository code.

## SRD 5.2.1 example — CC BY 4.0

Source: [official SRD landing page](https://www.dndbeyond.com/srd) and
[SRD 5.2.1 PDF](https://media.dndbeyond.com/compendium-images/srd/5.2/SRD_CC_v5.2.1.pdf).
The PDF's legal information on page 1 supplies the license and required attribution.
Implemented mechanics come from pages 6–8 and 16, not a separate commercial rulebook.

This work includes material from the System Reference Document 5.2.1 (“SRD 5.2.1”) by Wizards of the Coast LLC, available at https://www.dndbeyond.com/srd. The SRD 5.2.1 is licensed under the Creative Commons Attribution 4.0 International License, available at https://creativecommons.org/licenses/by/4.0/legalcode.

Changes: selected ability-check, saving-throw, and weapon-attack mechanics were reimplemented
as deterministic Python handlers with typed inputs. Example characters, prompts, API integration,
and tests are new. This is a partial implementation, not a full game engine or an endorsed product.
Class/spell content, setting material, character creation, action economy, death rules, and
conditions are not implemented. The module's name identifies its actual source version.

## Fate Condensed example — CC BY 3.0

Sources: [official licensing information](https://fate-srd.com/official-licensing-fate),
[CC BY guidance](https://fate-srd.com/official-licensing-fate/cc), and the
[official CC BY SRDs ZIP](https://fate-srd.com/downloads/CC-BY-SRDs.zip).
Implementation was checked against `CC-BY SRDs/Fate-Condensed-SRD-CC-BY.html` inside that ZIP,
as instructed by the official licensing page, rather than treating website presentation text
as the source document. The attribution below retains the names in that downloaded SRD.

Fate Condensed ©2020 Evil Hat Productions, LLC.

This work is based on Fate Condensed (found at http://www.faterpg.com/), a product of Evil Hat Productions, LLC, developed, authored, and edited by PK Sullivan, Lara Turner, Leonard Balsera, Fred Hicks, Richard Bellingham, Robert Hanz, Ryan Macklin, and Sophie Lagacé, and licensed for our use under the Creative Commons Attribution 3.0 Unported license (http://creativecommons.org/licenses/by/3.0/).

Source integrity at retrieval:

- ZIP SHA256: `24b5bbdfed0a87ea17837428df72bf4e9a1784c1a8f3e683030e7ddee9ea2134`.
- HTML SHA256: `a0f556ce2415fe4dc88ff71e56755bb92e113a52f8041bda69b759b63782544e`.

Changes: selected unmodified-roll overcome and new-aspect advantage mechanics were
reimplemented in Python. Four d3 rolls minus eight encode the same independent -1/0/+1
distribution as four Fate dice; the physical d3 faces remain journaled. Example characters,
prompts, storage representations, and tests are new. Costs and alternative outcomes require
GM adjudication and remain explicitly pending. Invocations, stunts, opposed actions, combat,
stress/consequences, and advancement are not implemented. No endorsement is claimed.

## Distribution notes

- [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) and
  [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) permit reuse/adaptation under their
  respective conditions. Keep the licenses' notices and links with redistributed derived material.
- Module manifests and exported prompts include source and attribution text plus modification
  notices. Keep credit text readable and at the same size as surrounding copyright text.
- The existing `generic-fantasy` scaffold is not labeled as an adaptation of these sources and
  has no third-party source license assigned by this task.
- These examples do not incorporate CoC material. Adding another system requires checking its
  actual reusable source and terms, rather than assuming its commercial rulebook is open content.
