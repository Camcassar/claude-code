"""Transcript post-processing — turn raw speech-to-text into what you meant.

Pipeline: strip filler words ("um", "uh") → apply the user's replacement
rules ("new line" → \\n, slang fixes) → optionally rewrite with Claude for
grammar/intent cleanup (requires ANTHROPIC_API_KEY and "ai_cleanup": true).
"""

from __future__ import annotations

import re

from .config import Config

FILLER_RE = re.compile(r"\b(?:um+|uh+|uhm+|erm+|mhm+)\b[,.]?\s*", re.IGNORECASE)
SPACE_RE = re.compile(r"\s{2,}")
ORPHAN_PUNCT_RE = re.compile(r"\s+([,.!?;:])")


def clean_transcript(text: str, config: Config) -> str:
    text = text.strip()
    if config.remove_fillers:
        text = FILLER_RE.sub("", text)
        text = ORPHAN_PUNCT_RE.sub(r"\1", SPACE_RE.sub(" ", text)).strip()
        # Re-capitalize if we stripped a leading "Um, ".
        if text and text[0].islower():
            text = text[0].upper() + text[1:]
    for old, new in config.replacements.items():
        text = text.replace(old, new)
        # Whisper often capitalizes the start of an utterance.
        text = text.replace(old.capitalize(), new)
    if config.ai_cleanup and text:
        text = _ai_cleanup(text, config)
    return text.strip()


def _ai_cleanup(text: str, config: Config) -> str:
    """Rewrite the transcript to the user's intended text via Claude."""
    try:
        import anthropic

        client = anthropic.Anthropic()
        system = (
            "You clean up voice-dictated text. Fix transcription errors, "
            "remove false starts and repeated words, and correct grammar and "
            "punctuation while preserving the speaker's meaning, tone, and "
            "wording as much as possible. Do not summarize, expand, or answer "
            "questions in the text. Reply with ONLY the cleaned text."
        )
        if config.dictionary:
            system += (
                " The speaker uses these names/terms (prefer these spellings): "
                + ", ".join(config.dictionary)
            )
        response = client.messages.create(
            model=config.ai_model,
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": text}],
        )
        cleaned = next(
            (block.text for block in response.content if block.type == "text"), ""
        ).strip()
        return cleaned or text
    except Exception as exc:
        print(f"AI cleanup skipped ({exc})")
        return text
