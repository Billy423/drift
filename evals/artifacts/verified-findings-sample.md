# drift report

## Verified findings — 6

- **path_exists** · `jupyter_server/i18n/README.md:32`
  - doc claims: notebook/i18n/
  - code truth: path not found in the scanned tree
  - stale path_exists claim 'notebook/i18n/' in jupyter_server/i18n/README.md
- **path_exists** · `jupyter_server/i18n/README.md:40`
  - doc claims: notebook/i18n/notebook.pot
  - code truth: path not found in the scanned tree
  - stale path_exists claim 'notebook/i18n/notebook.pot' in jupyter_server/i18n/README.md
- **path_exists** · `jupyter_server/i18n/README.md:42`
  - doc claims: notebook/i18n/nbui.pot
  - code truth: path not found in the scanned tree
  - stale path_exists claim 'notebook/i18n/nbui.pot' in jupyter_server/i18n/README.md
- **path_exists** · `jupyter_server/i18n/README.md:44`
  - doc claims: noteook/i18n/nbjs.pot
  - code truth: path not found in the scanned tree
  - stale path_exists claim 'noteook/i18n/nbjs.pot' in jupyter_server/i18n/README.md
- **path_exists** · `jupyter_server/i18n/README.md:53`
  - doc claims: babel_nbjs.cfg
  - code truth: path not found in the scanned tree
  - stale path_exists claim 'babel_nbjs.cfg' in jupyter_server/i18n/README.md
- **path_exists** · `jupyter_server/i18n/README.md:78`
  - doc claims: notebook/i18n/nbjs.json
  - code truth: path not found in the scanned tree
  - stale path_exists claim 'notebook/i18n/nbjs.json' in jupyter_server/i18n/README.md

## Ranked tier (candidates — UNVERIFIED) — 10

_Not certified by the replay gate. These are candidates the agent surfaced, banded by its own confidence that each claim still holds — read them as leads, never as findings._

### From the agent · SUSPECTED (confidence <= 0.2; not ranked within the band) — 7

- `jupyter_server/i18n/README.md`: jupyter notebook
  - Describes CLI behavior of the `jupyter notebook` command from a different (legacy) package, not jupyter_server itself; not a checkable repo artifact here.
- `jupyter_server/i18n/README.md`: notebook/templates/*.html
  - Path doesn't exist; actual templates directory is jupyter_server/templates/.
- `jupyter_server/i18n/README.md`: notebook/i18n/${LANG}/LC_MESSAGES
  - Templated path with placeholder and stale notebook/ prefix; not a concrete checkable literal, but structurally analogous to jupyter_server/i18n/zh_CN/LC_MESSAGES/ which does exist.
- `jupyter_server/i18n/README.md`: ${LANG}/LC_MESSAGES/notebook.mo
  - Templated compiled-message output path; no .mo files are actually checked into the repo (only .po under zh_CN).
- `jupyter_server/i18n/README.md`: ${LANG}/LC_MESSAGES/nbjs.json
  - Templated po2json output path invocation; not a fixed repo path to check.
- `jupyter_server/i18n/README.md`: All i18n-related commands are done from the related directory
  - General claim that all i18n tooling commands run from a single canonical i18n directory named notebook/i18n/; the real directory is jupyter_server/i18n/, so the framing is outdated repo-wide.
- `jupyter_server/i18n/README.md`: The translatable material for notebook is split into 3 `.pot` files
  - Only two of the three described .pot files (notebook.pot, nbui.pot) actually exist in jupyter_server/i18n/; the third (nbjs.pot) is absent, replaced by a compiled nbjs.json.

### From the agent · unexamined (confidence > 0.2; not ranked within the band) — 3

- `jupyter_server/i18n/README.md`: pip install babel
  - External tool install instruction, not a repo-internal artifact.
- `jupyter_server/i18n/README.md`: npm install -g po2json
  - External tool install instruction, not a repo-internal artifact.
- `jupyter_server/i18n/README.md`: po2json -p -F -f jed1.x -d nbjs ${LANG}/LC_MESSAGES/nbjs.po ${LANG}/LC_MESSAGES/nbjs.json
  - External command usage example with templated paths; not a concrete repo path or make/npm target.
