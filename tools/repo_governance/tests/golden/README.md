# Golden files

Expected extractor output for the fixtures next door. A golden file changing is either a
deliberate improvement or a regression, and the diff is the argument for which.

Goldens are written by the same canonical writer as real output — LF, UTF-8 without BOM,
sorted keys — so a comparison failure is always a content difference and never a formatting
artefact.

The blueprint also requires goldens for every supported governance version, so that an old
document form can still be validated during a bounded migration window.

Populated from Phase 3 onward, alongside the extractors.
