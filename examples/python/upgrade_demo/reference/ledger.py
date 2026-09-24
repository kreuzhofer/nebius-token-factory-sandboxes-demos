"""Known-good upgrade used only by automated tests, never by the live agent."""

import argparse
import json

from sqlalchemy import create_engine, text


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("init", "add", "report"))
    parser.add_argument("database")
    parser.add_argument("category", nargs="?")
    parser.add_argument("cents", nargs="?", type=int)
    args = parser.parse_args()
    engine = create_engine("sqlite:///" + args.database)
    with engine.begin() as connection:
        if args.action == "init":
            connection.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS expenses "
                    "(id INTEGER PRIMARY KEY, category TEXT NOT NULL, cents INTEGER NOT NULL)"
                )
            )
        elif args.action == "add":
            connection.execute(
                text("INSERT INTO expenses (category, cents) VALUES (:category, :cents)"),
                {"category": args.category, "cents": args.cents},
            )
        else:
            rows = connection.execute(text("SELECT category, cents FROM expenses")).fetchall()
            categories: dict[str, int] = {}
            for category, cents in rows:
                categories[category] = categories.get(category, 0) + cents
            print(
                json.dumps(
                    {
                        "categories": categories,
                        "total": sum(categories.values()),
                        "count": len(rows),
                    },
                    sort_keys=True,
                )
            )


if __name__ == "__main__":
    main()
