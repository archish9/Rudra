"""Generate the repo-root NOTICE from the bundle registry.

Generated rather than hand-written so attribution cannot drift as bundles
are added -- a test asserts the committed file matches this output.

On the MIT question (spec section 5): MIT requires that the copyright and
permission notice survive in copies. It does not require a statement of
modification -- that is Apache-2.0 section 4(b). Because Rudra's transform
runs at cache-build time, the corpus Rudra *distributes* is byte-identical
upstream and the modified copy exists only in the user's cache. The notice
states both facts rather than an obligation that is not there.
"""

from __future__ import annotations

from collections.abc import Sequence

from rudra.skills.bundle import Bundle

_HEADER = """Rudra
Copyright 2026 Archish Patel.

Licensed under the Apache License, Version 2.0. See LICENSE for the full text.

================================================================================
Third-party components
================================================================================

Rudra ships vendored copies of the third-party projects below. Each copy is
byte-identical to the upstream release named, and is frozen: Rudra performs
no version check and no update fetch at runtime.

Rudra renders a modified copy of these files into the user's cache directory
when it runs. The distributed copy in this repository is unmodified. The
rendered copy differs in exactly three ways:

  1. Cross-references between documents are rewritten from the upstream
     plugin-namespace form to filesystem paths Rudra's agents can read.
  2. One reference file describing Rudra's own tools is added, and listed in
     the upstream document that indexes such files.
  3. The frontmatter "description" field of two documents -- "brainstorming"
     and "using-superpowers" -- is replaced. That field is not documentation
     in this setting: the agent framework copies it verbatim into the system
     prompt of every agent that indexes the corpus, so an unconditional
     instruction there is issued to agents that cannot carry it out. The
     replacements say the same thing conditionally.

No other content is altered, added, or removed. Document bodies -- the
methodology itself -- are byte-identical to upstream in both the distributed
and the rendered copy.

Rudra's own planning prompts (src/rudra/agent/planner_agent.py) additionally
contain methodology adapted from the "brainstorming" document listed below,
used under its MIT licence.
"""

_BUNDLE_TEMPLATE = """
--------------------------------------------------------------------------------
{name} {version}
--------------------------------------------------------------------------------

Upstream:  {upstream}
Version:   {version}
Vendored:  {copied_on}
License:   {license}
{copyright}

Full license text: src/rudra/skills/bundles/{name}/{license_file}
Vendored files:    src/rudra/skills/bundles/{name}/{skills_root}/
Content manifest:  src/rudra/skills/bundles/{name}/MANIFEST.sha256
"""


def render_notice(bundles: Sequence[Bundle]) -> str:
    """The full NOTICE text for `bundles`, in registry order."""
    parts = [_HEADER]
    for bundle in bundles:
        parts.append(
            _BUNDLE_TEMPLATE.format(
                name=bundle.name,
                version=bundle.version,
                upstream=bundle.upstream,
                copied_on=bundle.copied_on,
                license=bundle.license,
                copyright=bundle.copyright,
                license_file=bundle.license_file,
                skills_root=bundle.skills_root,
            )
        )
    return "".join(parts)
