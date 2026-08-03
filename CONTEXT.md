# Wenyi Domain Context

## Translation stages

- **Formal translation**: the target text currently eligible for QA and export. A model proposal is not formal until its stage-specific acceptance rule passes.
- **Shadow translation**: a review-fix proposal evaluated without mutating the formal translation. Failed or uncertain proposals remain archived and never cross into formal state.
- **Stage archive**: the append-only history of first drafts, refinements, normalization, shadow proposals, and applied review fixes under `state/<book>/artifacts/`. Content-addressed input snapshots make each recorded version explainable from its visible glossary and narrative context.

## Translation knowledge

- **Visible glossary projection**: exact `source → target` mappings and narrative facts allowed at one chapter/segment position. A glossary alias is a retrieval spelling, not an enforceable whole-string translation.
- **Review candidate**: a model-reported issue before deterministic evidence validation. Only supported candidates enter the formal review report; dismissed candidates remain available as audit evidence.

## Invariants

1. Later narrative facts never enter an earlier visible glossary projection.
2. A terminology finding cites an exact source mapping; aliases cannot inherit a canonical target.
3. A severe review fix reaches formal translation only after length validation and a clean blind review of its shadow translation.
4. Stage archive records are append-only; current chapter state may advance, but prior model outputs are not overwritten.
