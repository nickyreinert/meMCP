#!/usr/bin/env python3
"""
scripts/manage_tokens.py — Token Management CLI
================================================

Usage:
  python scripts/manage_tokens.py add    --owner "Google-Recruiter" [--days 30]
  python scripts/manage_tokens.py remove --id <token_id>
  python scripts/manage_tokens.py list
  python scripts/manage_tokens.py stats  [--id <token_id>]

Commands:
  add     Create a new access token (generates a secure random value).
  remove  Revoke (deactivate) or permanently delete a token by its ID.
  list    Show all tokens with status and expiry.
  stats   Show usage logs — all tokens or one specific token.
"""

import argparse
import json
import secrets
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── Resolve DB path relative to this script ──────────────────────────────────

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "db" / "profile.db"


# ── Terminal colours (graceful no-op if not supported) ───────────────────────

class C:
    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    DIM    = "\033[2m"
    GREEN  = "\033[32m"
    YELLOW = "\033[33m"
    RED    = "\033[31m"
    CYAN   = "\033[36m"
    WHITE  = "\033[97m"

def _bold(s):  return f"{C.BOLD}{s}{C.RESET}"
def _dim(s):   return f"{C.DIM}{s}{C.RESET}"
def _green(s): return f"{C.GREEN}{s}{C.RESET}"
def _yellow(s):return f"{C.YELLOW}{s}{C.RESET}"
def _red(s):   return f"{C.RED}{s}{C.RESET}"
def _cyan(s):  return f"{C.CYAN}{s}{C.RESET}"


# ── DB helpers ────────────────────────────────────────────────────────────────

def get_conn() -> sqlite3.Connection:
    if not DB_PATH.exists():
        sys.exit(f"Database not found at {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_dt(iso: str) -> datetime:
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _status_label(row) -> str:
    if not row["is_active"]:
        return _dim("revoked ")
    expires = parse_dt(row["expires_at"])
    if datetime.now(timezone.utc) > expires:
        return _red("expired ")
    delta = expires - datetime.now(timezone.utc)
    days = delta.days
    if days <= 3:
        return _yellow(f"expires {days}d")
    return _green("active  ")


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_add(args):
    owner  = args.owner
    days   = args.days
    token  = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
    created = now_utc()

    conn = get_conn()
    cur = conn.execute(
        """
        INSERT INTO tokens (token_value, owner_name, expires_at, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (token, owner, expires, created),
    )
    conn.commit()
    token_id = cur.lastrowid
    conn.close()

    print()
    print(_bold("  Token created"))
    print(f"  {'ID':<14} {_cyan(str(token_id))}")
    print(f"  {'Owner':<14} {owner}")
    print(f"  {'Expires':<14} {expires[:10]}  ({days} days)")
    print(f"  {'Token':<14} {_bold(token)}")
    print()
    print(_dim("  Keep this value secret — it will not be shown again."))
    print()


def cmd_remove(args):
    token_id = args.id

    conn = get_conn()
    row = conn.execute(
        "SELECT id, owner_name, is_active FROM tokens WHERE id = ?", (token_id,)
    ).fetchone()

    if not row:
        conn.close()
        sys.exit(f"No token with ID {token_id}.")

    if args.hard:
        conn.execute("DELETE FROM tokens WHERE id = ?", (token_id,))
        action = _red("deleted permanently")
    else:
        conn.execute("UPDATE tokens SET is_active = 0 WHERE id = ?", (token_id,))
        action = _yellow("revoked (soft)")

    conn.commit()
    conn.close()

    print()
    print(f"  Token {_bold(str(token_id))} ({row['owner_name']}) — {action}")
    print()


def cmd_list(args):
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT
            t.id, t.owner_name, t.expires_at, t.is_active, t.created_at,
            COUNT(u.id) AS call_count
        FROM tokens t
        LEFT JOIN usage_logs u ON u.token_id = t.id
        GROUP BY t.id
        ORDER BY t.id
        """
    ).fetchall()
    conn.close()

    if not rows:
        print("\n  No tokens found.\n")
        return

    # Header
    print()
    print(
        f"  {_bold('ID'):<18}"
        f"{_bold('Status'):<20}"
        f"{_bold('Owner'):<25}"
        f"{_bold('Expires'):<14}"
        f"{_bold('Calls')}"
    )
    print("  " + "─" * 78)

    for row in rows:
        status  = _status_label(row)
        expires = row["expires_at"][:10]
        calls   = str(row["call_count"])
        owner   = row["owner_name"]
        rid     = str(row["id"])

        print(
            f"  {rid:<10}"
            f"{status:<20}"
            f"{owner:<25}"
            f"{expires:<14}"
            f"{calls}"
        )

    print()


def cmd_stats(args):
    conn = get_conn()

    if args.id:
        # Single token
        token = conn.execute(
            "SELECT id, owner_name, expires_at, is_active FROM tokens WHERE id = ?",
            (args.id,),
        ).fetchone()
        if not token:
            conn.close()
            sys.exit(f"No token with ID {args.id}.")

        logs = conn.execute(
            """
            SELECT endpoint_called, timestamp, input_args
            FROM usage_logs
            WHERE token_id = ?
            ORDER BY timestamp DESC
            LIMIT 50
            """,
            (args.id,),
        ).fetchall()
        conn.close()

        print()
        print(_bold(f"  Stats for token #{token['id']} — {token['owner_name']}"))
        print(f"  Status  : {_status_label(token)}")
        print(f"  Expires : {token['expires_at'][:10]}")
        print(f"  Total calls in log: {len(logs)}")
        print()

        if not logs:
            print(_dim("  No usage logged yet.\n"))
            return

        # Endpoint frequency
        freq: dict[str, int] = {}
        for log in logs:
            ep = log["endpoint_called"]
            freq[ep] = freq.get(ep, 0) + 1

        print(_bold("  Endpoint breakdown:"))
        for ep, count in sorted(freq.items(), key=lambda x: -x[1]):
            print(f"    {count:>4}×  {ep}")

        print()
        print(_bold(f"  Last {min(len(logs), 10)} requests:"))
        print(f"  {'Timestamp':<28}  {'Endpoint':<30}  Args")
        print("  " + "─" * 80)

        for log in logs[:10]:
            ts   = log["timestamp"][:19].replace("T", " ")
            ep   = log["endpoint_called"]
            args_raw = log["input_args"]
            if args_raw:
                try:
                    parsed = json.loads(args_raw)
                    args_str = json.dumps(parsed, ensure_ascii=False)
                    if len(args_str) > 40:
                        args_str = args_str[:37] + "…"
                except Exception:
                    args_str = args_raw[:40]
            else:
                args_str = _dim("—")
            print(f"  {ts:<28}  {ep:<30}  {args_str}")

        print()

    else:
        # All tokens summary
        rows = conn.execute(
            """
            SELECT
                t.id, t.owner_name, t.is_active, t.expires_at,
                COUNT(u.id)                                          AS total_calls,
                MAX(u.timestamp)                                     AS last_seen,
                SUM(CASE WHEN u.timestamp >= datetime('now','-7 days')
                         THEN 1 ELSE 0 END)                          AS calls_7d
            FROM tokens t
            LEFT JOIN usage_logs u ON u.token_id = t.id
            GROUP BY t.id
            ORDER BY total_calls DESC
            """
        ).fetchall()
        conn.close()

        if not rows:
            print("\n  No tokens found.\n")
            return

        # Top endpoints across all tokens
        conn2 = get_conn()
        top_eps = conn2.execute(
            """
            SELECT endpoint_called, COUNT(*) AS n
            FROM usage_logs
            GROUP BY endpoint_called
            ORDER BY n DESC
            LIMIT 10
            """
        ).fetchall()
        conn2.close()

        print()
        print(_bold("  Token Usage Summary"))
        print()
        print(
            f"  {_bold('ID'):<10}"
            f"{_bold('Status'):<20}"
            f"{_bold('Owner'):<25}"
            f"{_bold('Total'):<8}"
            f"{_bold('7d'):<6}"
            f"{_bold('Last Seen')}"
        )
        print("  " + "─" * 84)

        for row in rows:
            status    = _status_label(row)
            last_seen = (row["last_seen"] or "never")[:16].replace("T", " ")
            calls_7d  = str(row["calls_7d"] or 0)
            total     = str(row["total_calls"] or 0)
            owner     = row["owner_name"]
            rid       = str(row["id"])

            print(
                f"  {rid:<10}"
                f"{status:<20}"
                f"{owner:<25}"
                f"{total:<8}"
                f"{calls_7d:<6}"
                f"{last_seen}"
            )

        if top_eps:
            print()
            print(_bold("  Top endpoints (all tokens):"))
            for ep in top_eps:
                print(f"    {ep['n']:>5}×  {ep['endpoint_called']}")

        print()


# ── Entry point ───────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="manage_tokens",
        description="Manage meMCP access tokens.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="command", required=True)

    # add
    p_add = sub.add_parser("add", help="Create a new token")
    p_add.add_argument(
        "--owner", required=True,
        metavar="NAME",
        help='Label for who holds this token (e.g. "Google-Recruiter")',
    )
    p_add.add_argument(
        "--days", type=int, default=30,
        metavar="N",
        help="Number of days until expiry (default: 30)",
    )

    # remove
    p_rm = sub.add_parser("remove", help="Revoke or delete a token")
    p_rm.add_argument("--id", type=int, required=True, metavar="ID", help="Token ID")
    p_rm.add_argument(
        "--hard", action="store_true",
        help="Permanently delete instead of soft-revoke (also deletes usage logs)",
    )

    # list
    sub.add_parser("list", help="List all tokens with status")

    # stats
    p_stats = sub.add_parser("stats", help="Show usage logs")
    p_stats.add_argument(
        "--id", type=int, metavar="ID",
        help="Show stats for a specific token (omit for all-token summary)",
    )

    return p


def main():
    parser = build_parser()
    args   = parser.parse_args()

    dispatch = {
        "add":    cmd_add,
        "remove": cmd_remove,
        "list":   cmd_list,
        "stats":  cmd_stats,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
