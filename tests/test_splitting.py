"""Tests for import segment sizing and soft split balancing.

These tests protect the split behavior used when importing Project Gutenberg
texts into MorseBook playback segments.
"""

import app


def test_chars_for_seconds_uses_requested_wpm_without_large_floor():
    """Character targets should follow the requested seconds and WPM."""

    assert app.chars_for_seconds(90, 18) == 135
    assert app.chars_for_seconds(90, 40) == 300
    assert app.chars_for_seconds(180, 40) == 600


def test_split_segments_merges_short_heading_with_following_text():
    """Short headings should be merged with following prose."""

    text = "\n\n".join(
        [
            "Down the Rabbit-Hole",
            "Alice was beginning to get very tired of sitting by her sister on the bank, "
            "and of having nothing to do: once or twice she had peeped into the book her "
            "sister was reading, but it had no pictures or conversations in it.",
        ]
    )

    segments = app.split_segments(text, 300)

    assert len(segments) == 1
    assert "Down the Rabbit-Hole" in segments[0]
    assert "Alice was beginning" in segments[0]


def test_split_segments_merges_tiny_wrap_remainders():
    """Tiny wrap remainders should not become standalone segments."""

    text = (
        "Alice was beginning to get very tired of sitting by her sister on the bank, "
        "and of having nothing to do: once or twice she had peeped into the book her "
        "sister was reading, but it had no pictures or conversations in it, "
        '"and what is the use of a book," thought Alice "without pictures or '
        'conversations?" '
        "So she was considering in her own mind whether the pleasure of making a "
        "daisy-chain would be worth the trouble of getting up and picking the daisies."
    )

    segments = app.split_segments(text, 300)

    assert len(segments) > 1
    assert all(len(segment) >= 100 for segment in segments)
    assert "conversations?" in segments[-1]


def test_split_segments_keeps_oversized_merge_bounded():
    """Soft merging should stay within the configured hard overflow bound."""

    text = "\n\n".join(
        [
            "Brief note.",
            " ".join(["word"] * 95),
            " ".join(["tail"] * 90),
        ]
    )

    segments = app.split_segments(text, 300)

    assert all(len(segment) <= 405 for segment in segments)
    assert all(len(segment) >= 100 for segment in segments)
