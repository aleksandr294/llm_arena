#!/usr/bin/env python3
"""Minimal Kan (kan.bn) API client for managing tasks.

Usage:
  scripts/kan.py board                       # show lists and cards
  scripts/kan.py create "Title" [-d TEXT] [-l LIST]
  scripts/kan.py move CARD_ID LIST
  scripts/kan.py show CARD_ID
  scripts/kan.py comment CARD_ID "text"

LIST is a list name or public ID (default: "Open").
Config comes from .env.kan (see .env.kan.example) or the environment:
KAN_API_KEY and KAN_BOARD_ID are required, KAN_URL is optional.
"""

import argparse
import json
import os
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env.kan"
DEFAULT_URL = "http://localhost:3000/api/v1"
DEFAULT_LIST = "Open"


def load_env() -> dict[str, str]:
    """Environment variables, with .env.kan filling in missing ones."""
    env = dict(os.environ)
    if not ENV_FILE.exists():
        return env
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env.setdefault(key.strip(), value.strip())
    return env


class KanClient:
    def __init__(self, url: str, api_key: str, board_id: str) -> None:
        self.url = url
        self.api_key = api_key
        self.board_id = board_id

    @classmethod
    def from_env(cls) -> "KanClient":
        env = load_env()
        missing = [k for k in ("KAN_API_KEY", "KAN_BOARD_ID") if not env.get(k)]
        if missing:
            raise SystemExit(
                f"{', '.join(missing)} not set. Put it in {ENV_FILE.name} "
                f"(copy {ENV_FILE.name}.example) or export it."
            )
        return cls(
            url=env.get("KAN_URL", DEFAULT_URL),
            api_key=env["KAN_API_KEY"],
            board_id=env["KAN_BOARD_ID"],
        )

    def _request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> Any:
        request = urllib.request.Request(
            self.url + path,
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={
                "x-api-key": self.api_key,
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(request) as response:
            return json.load(response)

    def lists(self) -> list[dict[str, Any]]:
        board: dict[str, Any] = self._request("GET", f"/boards/{self.board_id}")
        return list(board["lists"])

    def resolve_list(self, name_or_id: str) -> str:
        lists = self.lists()
        for item in lists:
            if name_or_id in (item["publicId"], item["name"]):
                return str(item["publicId"])
        names = [item["name"] for item in lists]
        raise SystemExit(f"List {name_or_id!r} not found. Available: {names}")

    def create_card(self, title: str, description: str, list_name: str) -> str:
        card: dict[str, Any] = self._request(
            "POST",
            "/cards",
            {
                "title": title,
                "description": description,
                "listPublicId": self.resolve_list(list_name),
                "labelPublicIds": [],
                "memberPublicIds": [],
                "position": "end",
            },
        )
        return str(card.get("publicId", ""))

    def move_card(self, card_id: str, list_name: str) -> None:
        self._request(
            "PUT",
            f"/cards/{card_id}",
            {"listPublicId": self.resolve_list(list_name)},
        )

    def get_card(self, card_id: str) -> dict[str, Any]:
        card: dict[str, Any] = self._request("GET", f"/cards/{card_id}")
        return card

    def add_comment(self, card_id: str, text: str) -> None:
        self._request("POST", f"/cards/{card_id}/comments", {"comment": text})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("board", help="show lists and cards")

    create = commands.add_parser("create", help="create a card")
    create.add_argument("title")
    create.add_argument("-d", "--description", default="")
    create.add_argument("-l", "--list", default=DEFAULT_LIST)

    move = commands.add_parser("move", help="move a card to a list")
    move.add_argument("card")
    move.add_argument("list")

    show = commands.add_parser("show", help="print a card as JSON")
    show.add_argument("card")

    comment = commands.add_parser("comment", help="comment on a card")
    comment.add_argument("card")
    comment.add_argument("text")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    client = KanClient.from_env()

    if args.command == "board":
        for item in client.lists():
            print(f"## {item['name']} ({item['publicId']})")
            for card in item.get("cards", []):
                print(f"  {card['publicId']}  {card['title']}")
    elif args.command == "create":
        card_id = client.create_card(args.title, args.description, args.list)
        print(card_id, args.title)
    elif args.command == "move":
        client.move_card(args.card, args.list)
        print("moved")
    elif args.command == "show":
        card = client.get_card(args.card)
        print(json.dumps(card, ensure_ascii=False, indent=2))
    elif args.command == "comment":
        client.add_comment(args.card, args.text)
        print("commented")


if __name__ == "__main__":
    main()
