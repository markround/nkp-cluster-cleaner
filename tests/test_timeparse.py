"""
Tests for time parsing and formatting.

This is the arithmetic every deletion decision rests on, so it is pinned in
detail — in particular that everything comes back as timezone-aware UTC.
"""

from datetime import UTC, datetime, timedelta

import pytest

from nkp_cluster_cleaner.core.timeparse import (
    expiry_from,
    format_duration,
    now,
    parse_period,
    parse_timestamp,
)


class TestParsePeriod:
    @pytest.mark.parametrize(
        "period,expected",
        [
            ("1h", timedelta(hours=1)),
            ("48h", timedelta(hours=48)),
            ("1d", timedelta(days=1)),
            ("30d", timedelta(days=30)),
            ("1w", timedelta(weeks=1)),
            ("2w", timedelta(weeks=2)),
            ("1y", timedelta(days=365)),
            # Case-insensitive, surrounding whitespace tolerated.
            ("1D", timedelta(days=1)),
            ("  7d  ", timedelta(days=7)),
        ],
    )
    def test_supported_units(self, period, expected):
        assert parse_period(period) == expected

    @pytest.mark.parametrize(
        "period",
        [
            "",
            "d",
            "1",
            "1m",  # minutes/months are deliberately unsupported
            "-1d",
            "1.5d",
            "1 d",
            "abc",
            "1dd",
        ],
    )
    def test_malformed_periods_are_rejected(self, period):
        with pytest.raises(ValueError, match="Invalid format"):
            parse_period(period)


class TestParseTimestamp:
    def test_z_suffix_is_parsed_as_utc(self):
        parsed = parse_timestamp("2026-09-11T15:20:08Z")
        assert parsed == datetime(2026, 9, 11, 15, 20, 8, tzinfo=UTC)
        assert parsed.tzinfo is not None

    def test_naive_timestamps_are_assumed_utc(self):
        """The API always emits UTC, so a missing offset means UTC, not local."""
        assert parse_timestamp("2026-09-11T15:20:08") == parse_timestamp(
            "2026-09-11T15:20:08Z"
        )

    def test_explicit_offsets_are_normalised_to_utc(self):
        parsed = parse_timestamp("2026-09-11T16:20:08+01:00")
        assert parsed == datetime(2026, 9, 11, 15, 20, 8, tzinfo=UTC)

    def test_datetime_input_is_accepted(self):
        """Typed resources come back from the client as datetimes already."""
        value = datetime(2026, 9, 11, 15, 20, 8, tzinfo=UTC)
        assert parse_timestamp(value) == value

    def test_naive_datetime_input_is_assumed_utc(self):
        value = datetime(2026, 9, 11, 15, 20, 8)
        assert parse_timestamp(value).tzinfo == UTC

    def test_malformed_timestamp_is_rejected(self):
        with pytest.raises(ValueError, match="Invalid creation timestamp"):
            parse_timestamp("not-a-timestamp")


class TestExpiryFrom:
    def test_expiry_is_relative_to_creation_not_now(self):
        created = datetime(2026, 1, 1, tzinfo=UTC)
        assert expiry_from(created, "1d") == datetime(2026, 1, 2, tzinfo=UTC)

    def test_result_keeps_its_timezone(self):
        created = parse_timestamp("2026-01-01T00:00:00Z")
        assert expiry_from(created, "30d").tzinfo is not None


class TestFormatDuration:
    @pytest.mark.parametrize(
        "delta,expected",
        [
            (timedelta(days=5), "5d"),
            (timedelta(days=2, hours=3), "2d"),
            (timedelta(days=1), "1d"),
            # At one day, the hours are worth keeping: "1d" alone reads as
            # vaguer than the value actually is.
            (timedelta(days=1, hours=4), "1d 4h"),
            (timedelta(hours=7), "7h"),
            (timedelta(minutes=30), "0h"),
            (timedelta(seconds=-100), "0h"),
        ],
    )
    def test_formatting(self, delta, expected):
        assert format_duration(delta) == expected


def test_now_is_timezone_aware_utc():
    assert now().tzinfo == UTC
