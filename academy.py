import argparse
import ast
import datetime
import json
import random
from getpass import getpass

import httpx
import pytz
from pyfzf.pyfzf import FzfPrompt

VALUES = [
    *zip(range(2, 11), map(str, range(2, 11))),
    (11, "Jack"),
    (12, "Queen"),
    (13, "King"),
    (14, "Ace"),
]

SUITS = [
    ("S", "Spades"),
    ("C", "Clubs"),
    ("H", "Hearts"),
    ("D", "Diamonds"),
    ("A", "Carls"),
    ("I", "Heineken"),
]


client = httpx.Client(base_url="https://academy.beer")


def get_ms(prompt):
    seconds = float(input(prompt))
    return round(seconds * 1000)


def get_ms_from_timedelta(td):
    return round(td.total_seconds() * 1000)


def get_ordered_cards(player_count):
    return [(v[0], s[0]) for s in SUITS[:player_count] for v in VALUES]


def get_seed(cards):
    player_count = len(cards) // 13
    ordered_cards = get_ordered_cards(player_count)
    seed = []
    for i in range(len(cards) - 1, 0, -1):
        j = ordered_cards.index((cards[i]["value"], cards[i]["suit"]))
        seed.append(j)
        ordered_cards[i], ordered_cards[j] = ordered_cards[j], ordered_cards[i]

    return seed


def draw_remaining(game_id):
    r = client.get(f"/api/games/{game_id}/")
    r.raise_for_status()
    game_data = r.json()

    player_count = len(game_data["player_stats"])
    cards = set(get_ordered_cards(player_count))
    for c in game_data["cards"]:
        cards.remove((c["value"], c["suit"]))
        if c["chug_duration_ms"]:
            c["chug_end_start_delta_ms"] = (
                c["chug_start_start_delta_ms"] + c["chug_duration_ms"]
            )
        else:
            del c["chug_start_start_delta_ms"]

    last_start_delta_ms = game_data["cards"][-1]["start_delta_ms"]

    fzf = FzfPrompt()
    while cards:
        player_index = -len(cards) % player_count
        player_name = game_data["player_stats"][player_index]["username"]
        card = ast.literal_eval(
            fzf.prompt(sorted(cards), f'--header "Card for {player_name}"')[0]
        )
        cards.remove(card)

        time_delta = get_ms("Time since last card (s): ")
        start_delta_ms = last_start_delta_ms + time_delta
        card_data = {
            "value": card[0],
            "suit": card[1],
            "start_delta_ms": start_delta_ms,
        }

        last_start_delta_ms = start_delta_ms

        if card[0] == 14:
            before_begin = get_ms("Time before begin (s): ")
            chug_time = get_ms("Chug time (s): ")

            start = start_delta_ms + before_begin
            end = start + chug_time
            card_data["chug_start_start_delta_ms"] = start
            card_data["chug_end_start_delta_ms"] = end

            last_start_delta_ms = end

        game_data["cards"].append(card_data)

    game_data["has_ended"] = True
    game_data["description"] = input("Description: ")
    game_data["player_ids"] = [p["id"] for p in game_data["player_stats"]]
    game_data["player_names"] = [p["username"] for p in game_data["player_stats"]]

    return game_data


def create_game(tokens):
    r = client.post("/api/games/", json={"tokens": tokens, "official": True})
    r.raise_for_status()
    return r.json()


def submit_game(game_data, game_id, game_token):
    game_data["seed"] = get_seed(game_data["cards"])
    r = client.post(
        f"/api/games/{game_id}/update_state/",
        json=game_data,
        headers={"Authorization": f"GameToken {game_token}"},
    )
    r.raise_for_status()
    return r.json()


def login(username, password):
    r = client.post(
        "https://academy.beer/api-token-auth/",
        json={"username": username, "password": password,},
    )
    r.raise_for_status()
    return r.json()


def analog_create_cmd(args):
    usernames = []
    while True:
        username = input("Username: ").strip()
        if not username:
            break
        usernames.append(username)

    while True:
        try:
            dt = datetime.datetime.fromisoformat(input("Start datetime (YYYY-MM-DD HH:MM:SS): "))
            start_datetime = pytz.timezone("Europe/Copenhagen").localize(dt)
            break
        except ValueError:
            print("Invalid datetime.")

    while True:
        try:
            duration_str = input("Duration (HH:MM:SS): ")
            hours, minutes, seconds = map(int, duration_str.split(":"))
            duration = datetime.timedelta(hours=hours, minutes=minutes, seconds=seconds)
            break
        except ValueError:
            print("Invalid duration string.")

    game_data = {
        "start_datetime": start_datetime.isoformat(),
        "official": True,
        "player_names": usernames,
        "has_ended": True,
        "description": "Offline game",
    }

    cards_left = set(get_ordered_cards(len(usernames)))
    turn_duration = duration / (len(cards_left) + len(usernames))

    card_values = {}
    for u in usernames:
        card_values[u] = [int(x) for x in input(f"{u} card values: ").split(",")]
        assert len(card_values[u]) == 13

    cards = []

    start_delta = datetime.timedelta()
    for i in range(13):
        for u in usernames:
            start_delta += turn_duration
            value = card_values[u][i]
            value, suit = random.choice([c for c in cards_left if c[0] == value])
            cards_left.remove((value, suit))
            cards.append({
                "value": value,
                "suit": suit,
                "start_delta_ms": get_ms_from_timedelta(start_delta),
            })

            if value == 14:
                start_delta += turn_duration
                print()
                seconds = float(input(f"Chug duration for {u}: "))
                cards[-1]["chug_start_start_delta_ms"] = get_ms_from_timedelta(start_delta - datetime.timedelta(seconds=seconds))
                cards[-1]["chug_end_start_delta_ms"] = get_ms_from_timedelta(start_delta)


    game_data["cards"] = cards
    game_data["seed"] = get_seed(cards)

    with open("game_data.json", "w") as f:
        json.dump(game_data, f)


def new_game_cmd(args):
    usernames = []
    tokens = []
    while True:
        try:
            username = input("Username: ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        password = getpass()
        try:
            login_data = login(username, password)
            tokens.append(login_data["token"])
        except httpx.HTTPError:
            print("Wrong username/password")

    game_data = create_game(tokens)
    game_token = game_data["token"]
    game_id = game_data["id"]

    start_time = input("Start time in UTC (YYYY-mm-dd HH:mm): ")

    game_data = draw_remaining(game_id)

    submit_game(game_data, game_id, game_token)


def continue_cmd(args):
    if args.game_file:
        game_data = json.loads(args.game_file.read())
    else:
        game_data = draw_remaining(args.game_id)
        with open("game_data.json", "w") as f:
            json.dump(game_data, f)

    submit_game(game_data, args.game_id, args.game_token)


def get_milliseconds(td):
    return td.seconds * 1000 + td.microseconds // 1000


def fromisoformat(s):
    if s[-1] == "Z":
        return pytz.utc.localize(datetime.datetime.fromisoformat(s[:-1]))

    return datetime.datetime.fromisoformat(s)


def old_api_cmd(args):
    game_data = json.load(args.game_file)
    start_datetime = fromisoformat(game_data["start_datetime"])
    for c in game_data["cards"]:
        if "drawn_datetime" not in c:
            continue

        c["start_delta_ms"] = get_milliseconds(
            fromisoformat(c["drawn_datetime"]) - start_datetime
        )
        chug_duration = c.get("chug_duration_ms")
        if chug_duration:
            t = c["start_delta_ms"] + 2137
            c["chug_start_start_delta_ms"] = t
            c["chug_end_start_delta_ms"] = t + chug_duration

    game_data["has_ended"] = True

    json.dump(game_data, args.output_file)


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="cmd", required=True)

    analog_create_parser = subparsers.add_parser("analog_create")
    analog_create_parser.set_defaults(func=analog_create_cmd)

    new_game_parser = subparsers.add_parser("new_game")
    new_game_parser.set_defaults(func=new_game_cmd)

    continue_parser = subparsers.add_parser("continue")
    continue_parser.add_argument("game_id", type=int)
    continue_parser.add_argument("game_token")
    continue_parser.add_argument("--game-file", type=argparse.FileType("r"))
    continue_parser.set_defaults(func=continue_cmd)

    old_api_parser = subparsers.add_parser("old_api")
    old_api_parser.add_argument("game_file", type=argparse.FileType("r"))
    old_api_parser.add_argument("output_file", type=argparse.FileType("w"))
    old_api_parser.set_defaults(func=old_api_cmd)

    args = parser.parse_args()
    try:
        args.func(args)
    except httpx.HTTPError as e:
        print(f"Got unexepected error: {e}")
        print(e.response.json())


if __name__ == "__main__":
    main()
