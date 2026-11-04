"""Demo data: a made-up client to try Ringkeeper on before connecting anything real.

"Try it with demo data" adds the client Demo Co with one project, Demo shop:
a folder of sample files (in the workspaces folder, like any project's) and a
small SQLite shop database in the data folder, connected as a production
database, so a change to it waits for approval like a real one would. One
click removes all of it, including the demo's tasks and conversations.

What was made is remembered in settings_kv, so removing touches only that.
"""

import contextlib
import os
import shutil
import sqlite3
import stat
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from ringkeeper.adapters import sqlite_file
from ringkeeper.config import Settings
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import (
    Decision,
    DecisionStatus,
    Env,
    InstanceType,
    Setting,
    Task,
    Thread,
)
from ringkeeper.schemas import BindingIn, InstanceIn, ProjectIn
from ringkeeper.services import bindings, clients, instances, projects, undo, workspaces
from ringkeeper.services.secrets_in import CONNECTION_REF
from ringkeeper.vault import Vault

KEY = "demo"
CLIENT = "Demo Co"
PROJECT = "demo-shop"
CONNECTION = "demo-shop-db"
FOLDER = "ringkeeper-demo-shop"

# Things to try, once the demo is there (the second one waits for approval).
TRY = (
    "List the tables in Demo shop's database",
    "Which customers ordered more than once? Use Demo shop's database",
    "Mark order 1007 as shipped in Demo shop's database",
)


@dataclass(frozen=True)
class Demo:
    client: str
    project: str
    connection: str
    folder: str
    database: str


def current(session: Session) -> Demo | None:
    row = session.get(Setting, KEY)
    return Demo(**row.value) if row is not None else None


def database_path(settings: Settings) -> Path:
    return (settings.data_dir / "demo" / "shop.db").absolute()


def create(session: Session, settings: Settings, vault: Vault) -> Demo:
    """Add the demo client, project, folder and database. Refused if it's there already,
    or if a project or connection already has the demo's name."""
    if current(session) is not None:
        raise RuleViolationError("The demo is already there.")
    if projects.find_project(session, PROJECT) or instances.find_instance(session, CONNECTION):
        raise RuleViolationError(
            f"A project or connection named {PROJECT} or {CONNECTION} already exists, so "
            "the demo can't use those names. Rename yours to try the demo."
        )
    folder = workspaces.choose(settings, FOLDER, PROJECT)
    database = database_path(settings)
    if folder.exists() and any(folder.iterdir()):
        raise RuleViolationError(
            f"The folder {folder} already has files in it, so the demo won't use it. Move "
            "or rename it to try the demo."
        )
    # A repository first, so the sample files are on main and tasks work on branches.
    workspaces.prepare(settings, folder)
    _write_folder(folder)
    workspaces.commit(settings, folder, list(FILES), "Demo Co's sample files")
    _write_database(database)
    owner = clients.ensure(session, CLIENT)
    projects.create_project(
        session,
        ProjectIn(
            name=PROJECT,
            owner=owner,
            description=(
                "Demo Co's online shop (made-up sample data): customers, products and "
                "orders in its database, and notes and an export in its folder."
            ),
            aliases=["demo", "shop"],
            workspace_path=str(folder),
        ),
    )
    ref = CONNECTION_REF.format(name=CONNECTION)
    vault.store(ref, f"{sqlite_file.PREFIX}{database.as_posix()}")
    instances.create_instance(
        session,
        InstanceIn(
            name=CONNECTION,
            title="Demo shop database",
            type=InstanceType.SUPABASE,
            env=Env.PROD,  # so a change waits for approval, like a real client's
            secret_ref=ref,
            owner=owner,
            config={"demo": True},
        ),
    )
    bindings.set_binding(session, BindingIn(project=PROJECT, instance=CONNECTION))
    demo = Demo(owner, PROJECT, CONNECTION, str(folder), str(database))
    session.add(Setting(key=KEY, value=demo.__dict__))
    session.flush()
    return demo


def remove(session: Session, vault: Vault) -> None:
    """Remove everything the demo added: its tasks and their conversations, the
    project, the connection and its secret, the client, the folder and the database."""
    demo = current(session)
    if demo is None:
        raise RuleViolationError("There's no demo to remove.")
    busy = session.scalars(
        select(Task.id).where(Task.project == demo.project, Task.status.in_(projects.BUSY))
    ).all()
    if busy:
        raise RuleViolationError(
            "A demo task is still working or waiting to start. Stop it (on its page), then "
            "remove the demo."
        )
    session.execute(
        update(Decision)
        .where(Decision.project == demo.project, Decision.status == DecisionStatus.PENDING)
        .values(status=DecisionStatus.EXPIRED, decided_at=datetime.now(UTC))
    )
    threads = set(
        session.scalars(select(Task.thread_id).where(Task.project == demo.project)).all()
    ) - {None}
    session.execute(delete(Task).where(Task.project == demo.project))
    # A conversation goes too when the demo was all it was about.
    for thread in threads:
        if not session.scalar(select(Task.id).where(Task.thread_id == thread).limit(1)):
            session.execute(delete(Thread).where(Thread.id == thread))
    if projects.find_project(session, demo.project) is not None:
        projects.remove_project(session, demo.project)
    instance = instances.find_instance(session, demo.connection)
    if instance is not None:
        instances.remove_instance(session, demo.connection, force=True)
        vault.delete(instance.secret_ref)
    if clients.find(session, demo.client) is not None:
        # The user added something of their own to Demo Co: then the client stays.
        with contextlib.suppress(RuleViolationError):
            clients.delete(session, demo.client)
    session.execute(delete(Setting).where(Setting.key == KEY))
    session.flush()
    _delete_folder(Path(demo.folder))
    undo.remove_project(demo.project)
    Path(demo.database).unlink(missing_ok=True)


def _delete_folder(folder: Path) -> None:
    """The folder and its git repository (whose files Windows marks read-only)."""

    def writable(retry: Callable[[str], object], path: str, _error: BaseException) -> None:
        os.chmod(path, stat.S_IWRITE)
        retry(path)

    if folder.exists():
        shutil.rmtree(folder, onexc=writable)


# --- The sample data ---

CUSTOMERS = [
    (1, "Ana Torres", "ana@example.com", "Lisbon"),
    (2, "Ben Okafor", "ben@example.com", "Lagos"),
    (3, "Chloe Martin", "chloe@example.com", "Lyon"),
    (4, "Dev Patel", "dev@example.com", "Pune"),
    (5, "Elena Rossi", "elena@example.com", "Turin"),
    (6, "Felix Wagner", "felix@example.com", "Graz"),
    (7, "Grace Kim", "grace@example.com", "Busan"),
    (8, "Hugo Silva", "hugo@example.com", "Porto"),
]
PRODUCTS = [
    (1, "Ceramic mug", "kitchen", 14.00, 120),
    (2, "Linen apron", "kitchen", 32.00, 40),
    (3, "Oak cutting board", "kitchen", 45.00, 18),
    (4, "Wool throw", "home", 89.00, 12),
    (5, "Brass candle holder", "home", 26.00, 55),
    (6, "Notebook, dotted", "stationery", 9.50, 300),
    (7, "Fountain pen", "stationery", 58.00, 25),
]
# id, customer, days ago, status, [(product, quantity)]
ORDERS: list[tuple[int, int, int, str, list[tuple[int, int]]]] = [
    (1001, 1, 30, "delivered", [(1, 2), (6, 1)]),
    (1002, 2, 27, "delivered", [(4, 1)]),
    (1003, 3, 21, "delivered", [(3, 1), (2, 1)]),
    (1004, 1, 14, "delivered", [(7, 1)]),
    (1005, 4, 9, "shipped", [(5, 2)]),
    (1006, 5, 6, "shipped", [(1, 4)]),
    (1007, 6, 3, "paid", [(4, 1), (5, 1)]),
    (1008, 3, 2, "paid", [(6, 3), (7, 1)]),
    (1009, 7, 1, "paid", [(2, 2)]),
    (1010, 8, 0, "cancelled", [(3, 1)]),
]


def _write_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    today = datetime.now(UTC).date()
    prices = {p[0]: p[3] for p in PRODUCTS}
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE customers (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE,
                city TEXT NOT NULL
            );
            CREATE TABLE products (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL,
                price REAL NOT NULL, stock INTEGER NOT NULL
            );
            CREATE TABLE orders (
                id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id),
                ordered_on TEXT NOT NULL, status TEXT NOT NULL, total REAL NOT NULL
            );
            CREATE TABLE order_items (
                order_id INTEGER NOT NULL REFERENCES orders(id),
                product_id INTEGER NOT NULL REFERENCES products(id),
                quantity INTEGER NOT NULL, PRIMARY KEY (order_id, product_id)
            );
            """
        )
        conn.executemany("INSERT INTO customers VALUES (?, ?, ?, ?)", CUSTOMERS)
        conn.executemany("INSERT INTO products VALUES (?, ?, ?, ?, ?)", PRODUCTS)
        for ident, customer, ago, status, items in ORDERS:
            total = round(sum(prices[p] * q for p, q in items), 2)
            day = (today - timedelta(days=ago)).isoformat()
            conn.execute(
                "INSERT INTO orders VALUES (?, ?, ?, ?, ?)", (ident, customer, day, status, total)
            )
            conn.executemany(
                "INSERT INTO order_items VALUES (?, ?, ?)", [(ident, p, q) for p, q in items]
            )
        conn.commit()
    finally:
        conn.close()


FILES: dict[str, str] = {
    "README.md": """\
# Demo Co shop

Sample files for trying Ringkeeper. Demo Co, its customers and its orders are made up.

- `orders-export.csv`: last month's orders, as exported from the shop
- `notes/customer-feedback.md`: what customers said
- `site/index.html`: the shop's front page

The shop's database is the demo-shop-db connection (customers, products, orders).
Changing it waits for your approval, like a real client's production database.
""",
    "orders-export.csv": """\
order,customer,date,status,total
1001,Ana Torres,30 days ago,delivered,37.50
1002,Ben Okafor,27 days ago,delivered,89.00
1003,Chloe Martin,21 days ago,delivered,77.00
1004,Ana Torres,14 days ago,delivered,58.00
1005,Dev Patel,9 days ago,shipped,52.00
""",
    "notes/customer-feedback.md": """\
# Customer feedback

- Ana: loves the mugs, asked for a matching saucer.
- Ben: the wool throw arrived a day late; happy otherwise.
- Chloe: the cutting board's oil instructions were missing from the box.
- Dev: asked whether the candle holders come in black.
""",
    "site/index.html": """\
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Demo Co</title></head>
<body>
  <h1>Demo Co</h1>
  <p>Things for the kitchen, the home and the desk.</p>
  <p>Free shipping over 50.</p>
</body>
</html>
""",
}


def _write_folder(folder: Path) -> None:
    for name, text in FILES.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def summary(demo: Demo | None) -> dict[str, Any] | None:
    if demo is None:
        return None
    return {
        "client": CLIENT,  # its name, to show
        "project": demo.project,
        "connection": demo.connection,
        "folder": demo.folder,
        "try": list(TRY),
    }
