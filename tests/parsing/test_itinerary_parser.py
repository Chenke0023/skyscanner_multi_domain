from skyscanner_multi_domain.parsing.itinerary_parser import (
    match_itinerary_legs,
    parse_itinerary_legs,
)


def test_parse_one_way_direct_flight() -> None:
    legs = parse_itinerary_legs("08:10 11:25 Non-stop 3h 15m CNY 1,280")

    assert legs == [
        {
            "direction": "outbound",
            "departure_time": "08:10",
            "arrival_time": "11:25",
            "stop_count": 0,
            "duration_minutes": 195,
        }
    ]


def test_parse_round_trip_keeps_each_leg_separate() -> None:
    legs = parse_itinerary_legs(
        "去程 08:10 11:25 直飞 3小时15分 返程 18:30 23:10 1 stop 4h 40m HK$2,480",
        round_trip=True,
    )

    assert [leg["direction"] for leg in legs] == ["outbound", "return"]
    assert legs[0]["duration_minutes"] == 195
    assert legs[0]["stop_count"] == 0
    assert legs[1]["duration_minutes"] == 280
    assert legs[1]["stop_count"] == 1


def test_parse_multiple_stops_and_cross_day_arrival() -> None:
    legs = parse_itinerary_legs("22:45 07:10 +1 2 stops 10 hrs 25 mins £430")

    assert legs[0]["departure_time"] == "22:45"
    assert legs[0]["arrival_time"] == "07:10"
    assert legs[0]["stop_count"] == 2
    assert legs[0]["duration_minutes"] == 625


def test_missing_optional_fields_are_not_guessed() -> None:
    legs = parse_itinerary_legs("06:05 09:30 CNY 900")

    assert legs[0]["stop_count"] is None
    assert legs[0]["duration_minutes"] is None
    assert parse_itinerary_legs("CNY 900 · time unavailable") == []


def test_price_mismatch_does_not_attach_another_cards_itinerary() -> None:
    cards = [
        {"priceText": "CNY 1,000", "cardText": "08:00 10:00 Non-stop 2h CNY 1,000"},
        {"priceText": "CNY 1,200", "cardText": "09:00 12:00 1 stop 3h CNY 1,200"},
    ]

    assert match_itinerary_legs(cards, 1_100) == []
    assert match_itinerary_legs(cards, 1_200)[0]["departure_time"] == "09:00"


def test_same_price_prefers_the_most_complete_card() -> None:
    cards = [
        {"priceText": "$500", "cardText": "08:00 11:00 $500"},
        {"priceText": "$500", "cardText": "08:00 11:00 Direct 3 hours $500"},
    ]

    legs = match_itinerary_legs(cards, 500)

    assert legs[0]["stop_count"] == 0
    assert legs[0]["duration_minutes"] == 180
