"""Donor matching hierarchy, without calling DonorPerfect."""

import logging

from target_donorperfect.sinks import DonorsSink


def _sink(query):
    sink = DonorsSink.__new__(DonorsSink)
    sink.logger = logging.getLogger("donor-match-test")
    sink._query_donors = query
    return sink


def _email_sink(candidates):
    def query(where):
        assert where.startswith("email=")
        return candidates

    return _sink(query)


def test_unique_email_matches_without_other_fields():
    sink = _email_sink([
        {"donor_id": "10", "first_name": "Ada", "last_name": "Lovelace", "zip": "10001", "email": "ada@example.com"},
    ])
    assert sink.find_existing_donor_id({"email": "ada@example.com", "last_name": "Someone Else"}) == "10"


def test_email_and_last_name_picks_one_of_several_emails():
    sink = _email_sink([
        {"donor_id": "1", "first_name": "Ann", "last_name": "Smith", "zip": "11111", "email": "shared@example.com"},
        {"donor_id": "2", "first_name": "Bob", "last_name": "Jones", "zip": "22222", "email": "shared@example.com"},
    ])
    assert sink.find_existing_donor_id({
        "email": "shared@example.com",
        "last_name": "jones",
    }) == "2"


def test_zip_breaks_an_email_and_last_name_tie():
    sink = _email_sink([
        {"donor_id": "1", "first_name": "Cara", "last_name": "Lee", "zip": "33333", "email": "lee@example.com"},
        {"donor_id": "2", "first_name": "Cara", "last_name": "Lee", "zip": "44444-1234", "email": "lee@example.com"},
    ])
    assert sink.find_existing_donor_id({
        "email": "lee@example.com",
        "last_name": "Lee",
        "zip": "44444",
    }) == "2"


def test_first_name_breaks_an_email_last_name_and_zip_tie():
    sink = _email_sink([
        {"donor_id": "1", "first_name": "Pat", "last_name": "West", "zip": "55555", "email": "west@example.com"},
        {"donor_id": "2", "first_name": "Quinn", "last_name": "West", "zip": "55555", "email": "west@example.com"},
    ])
    assert sink.find_existing_donor_id({
        "email": "west@example.com",
        "last_name": "West",
        "zip": "55555",
        "first_name": "Quinn",
    }) == "2"


def test_full_duplicate_updates_oldest_donor():
    sink = _email_sink([
        {"donor_id": "2", "first_name": "Alex", "last_name": "River", "zip": "66666", "email": "dup@example.com"},
        {"donor_id": "1", "first_name": "Alex", "last_name": "River", "zip": "66666", "email": "dup@example.com"},
    ])
    assert sink.find_existing_donor_id({
        "email": "dup@example.com",
        "last_name": "River",
        "zip": "66666",
        "first_name": "Alex",
    }) == "1"


def test_last_name_matching_nobody_does_not_update_an_email_match():
    sink = _email_sink([
        {"donor_id": "1", "first_name": "Ann", "last_name": "Smith", "zip": "11111", "email": "shared@example.com"},
        {"donor_id": "2", "first_name": "Bob", "last_name": "Jones", "zip": "22222", "email": "shared@example.com"},
    ])
    assert sink.find_existing_donor_id({
        "email": "shared@example.com",
        "last_name": "Nobody",
        "zip": "11111",
        "first_name": "Ann",
    }) is None


def test_missing_tiebreaker_does_not_match():
    sink = _email_sink([
        {"donor_id": "1", "first_name": "Ann", "last_name": "Smith", "zip": "11111", "email": "shared@example.com"},
        {"donor_id": "2", "first_name": "Bob", "last_name": "Jones", "zip": "22222", "email": "shared@example.com"},
    ])
    assert sink.find_existing_donor_id({"email": "shared@example.com"}) is None


def test_no_email_does_not_match_on_name_and_zip():
    def query(where):
        raise AssertionError(f"unexpected query: {where}")

    sink = _sink(query)
    assert sink.find_existing_donor_id({
        "first_name": "noel",
        "last_name": "emailless",
        "zip": "77777-0001",
    }) is None


def test_unknown_email_does_not_match_on_name_and_zip():
    def query(where):
        assert where.startswith("email=")
        return []

    sink = _sink(query)
    assert sink.find_existing_donor_id({
        "email": "new@example.com",
        "first_name": "noel",
        "last_name": "emailless",
        "zip": "77777",
    }) is None
