"""Does the request text authorize deletion? (POLICY.md §9)

Zero-model rule layer. It is the fallback (switch S5) and the cross-check for the model
classifier added later: explicit non-deletion wording wins, otherwise a deletion request is
assumed — every deletion-free request in the data is phrased as an explicit "keep/don't delete".
"""

from __future__ import annotations

import re

# Phrases that make a request explicitly NOT a deletion, whatever other words it contains.
NON_DELETION = re.compile(
    r"not asking for deletion|not a request to (erase|delete)|do(n't| not) (delete|close|change|remove)"
    r"|without removing|leave (the|my) (account|profile)|keep the account|still (want|need|use)"
    r"|continue using|marketing lists only|lists only|leave my profile|stop the promotional"
    r"|in a portable format|copy of last year|send me a copy of the information|what personal information you hold"
    r"|replace it with the new one|reset(ting)? the login|correct it|change the email address",
    re.IGNORECASE,
)


def authorizes_deletion(text: str) -> tuple[bool, str]:
    m = NON_DELETION.search(text)
    if m:
        return False, f"explicit non-deletion wording: {m.group(0)!r}"
    return True, "deletion request (no non-deletion wording)"
