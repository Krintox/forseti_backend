"""
Publish the generated artifacts into the deployable backend repository.

Why this exists
---------------
FORSETI is developed as a monorepo (`Forseti/`) but DEPLOYS from a nested
repository (`Forseti/backend/`, pushed to forseti_backend). The pipeline writes
to `Forseti/artifacts/`; only `Forseti/backend/artifacts/` is tracked by the
repo that actually ships.

Those two diverged, and nothing noticed for six days. The public API kept
serving:

    "Per-transaction ML cannot detect cross-rail splitting"   (retracted)
    hybrid ML cross-rail recall, family held out:  0.0        (now 0.8281)
    PR-AUC 0.8882                                            (now 0.9209)
    DTL feature lift +0.1378                                 (now +0.2302)

- and, worse than any of the text, `models/forseti_model.joblib` from before the
categorical-leak fix, so the deployed service was scoring live traffic with the
leaked model.

Every one of those numbers had been corrected in the repo. None of the
corrections were in the artifact tree that deploys, so redeploying would have
changed nothing. `paths.py` already carries a comment about an earlier version
of this exact bug ("the training pipeline wrote to backend/artifacts/ while the
API read from ./artifacts/ and silently served stale fallbacks"); it came back
pointing the other way.

    python scripts/sync_deployable_artifacts.py            # copy + report
    python scripts/sync_deployable_artifacts.py --check    # exit 1 if diverged

Direction of truth
------------------
`backend/artifacts/` is canonical, because it is the only copy that can reach a
judge. `paths.py` resolves it first, so the pipeline writes there, the API reads
there, and a deploy ships it. The monorepo-root `artifacts/` is now a mirror
kept identical for anything still pointing at it.

The --check mode is wired into scripts/check_claims.py, so the gate that already
guards prose against the artifacts now also guards the deployed artifacts
against the local ones.
"""

from __future__ import annotations

import argparse
import filecmp
import hashlib
import os
import shutil
import sys
from typing import Dict, List, Tuple

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# CANONICAL is the tree that actually ships: it is tracked by the backend
# repository, it is what a deployed instance reads, and since `paths.py` learned
# to resolve it first, it is also what the pipeline writes and what the local
# API serves. One tree, four consumers.
#
# MIRROR is the historical monorepo-root copy. Nothing should depend on it any
# more, but docs, older scripts and muscle memory still point there, so it is
# kept byte-identical rather than deleted out from under anyone.
CANONICAL = os.path.join(REPO_ROOT, "backend", "artifacts")
MIRROR = os.path.join(REPO_ROOT, "artifacts")

SOURCE = CANONICAL
TARGET = MIRROR

# Per-run event logs. They accumulate without bound, say nothing about the
# model, and would make every deploy a large diff.
EXCLUDED_TOP_LEVEL = {"events"}


def _digest(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _inventory(root: str) -> Dict[str, str]:
    """Relative path -> content digest, for everything deployable."""
    found: Dict[str, str] = {}
    if not os.path.isdir(root):
        return found
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        top = rel_dir.split("/")[0]
        if top in EXCLUDED_TOP_LEVEL:
            dirnames[:] = []
            continue
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            found[rel] = _digest(full)
    return found


def diff() -> Tuple[List[str], List[str], List[str]]:
    """Returns (missing_in_target, differing, extra_in_target)."""
    src, dst = _inventory(SOURCE), _inventory(TARGET)
    missing = sorted(k for k in src if k not in dst)
    differing = sorted(k for k in src if k in dst and src[k] != dst[k])
    extra = sorted(k for k in dst if k not in src)
    return missing, differing, extra


def sync() -> int:
    missing, differing, extra = diff()
    copied = 0
    for rel in missing + differing:
        s = os.path.join(SOURCE, rel.replace("/", os.sep))
        t = os.path.join(TARGET, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(t), exist_ok=True)
        shutil.copy2(s, t)
        copied += 1
        print(f"  published  {rel}")
    for rel in extra:
        print(f"  ORPHAN     {rel}  (in backend/artifacts but not generated - left in place)")
    return copied


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Publish generated artifacts into the deployable backend repo",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="do not copy; exit 1 if the deployable copy differs from the generated one",
    )
    args = parser.parse_args()

    if not os.path.isdir(SOURCE):
        print(f"No canonical artifact tree at {SOURCE}. Run `python tasks.py all` first.")
        return 1

    missing, differing, extra = diff()

    if args.check:
        if not missing and not differing:
            print(f"Deployable artifacts are in sync ({len(_inventory(SOURCE))} files).")
            return 0
        print("DEPLOY SKEW - backend/artifacts/ does not match the generated artifacts.\n")
        print("The two artifact trees have diverged. backend/artifacts/ is the one")
        print("that ships, so a split here is what puts retracted numbers on a")
        print("public URL while the repository looks correct.\n")
        for rel in missing:
            print(f"  MISSING from the mirror:  {rel}")
        for rel in differing:
            print(f"  DIVERGED in the mirror:   {rel}")
        print("\nFix: python scripts/sync_deployable_artifacts.py")
        return 1

    print(f"Publishing canonical {SOURCE}\n        -> mirror {TARGET}")
    copied = sync()
    if copied:
        print(f"\n{copied} file(s) mirrored. The canonical tree is backend/artifacts/ - "
              "commit and redeploy THAT for changes to reach the public URL.")
    else:
        print("\nAlready in sync; nothing to publish.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
