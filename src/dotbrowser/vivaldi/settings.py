"""Vivaldi settings -- thin wrapper that adds prefs-schema awareness.

What this layer adds on top of ``_base.settings``:

* ``apply`` time: enum-name strings (``"left"``) get coerced to the int
  value Vivaldi actually stores (``1``).  Type mismatches and unknown
  keys are surfaced before the write happens.
* ``settings search`` and ``settings describe`` subcommands -- query
  the prefs schema by keyword or look up a single key.

Both features require ``prefs_definitions.json`` from the Vivaldi
install directory (see ``schema.py``).  When the schema is unavailable,
``apply`` falls back to the pre-schema behavior and the new
subcommands print a clear "schema not found" message.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotbrowser._base import settings as _base
from dotbrowser._base.utils import Plan, find_preferences, load_prefs
from dotbrowser.vivaldi import schema as _schema

NAMESPACE = _base.NAMESPACE

# Re-export internals that tests may use
_MISSING = _base._MISSING
_split_key = _base._split_key
_get_value = _base._get_value
_set_value = _base._set_value
_pop_value = _base._pop_value
_is_mac_protected = _base._is_mac_protected
_validate_table = _base._validate_table
_get_managed_keys = _base._get_managed_keys
_format_toml_value = _base._format_toml_value
diff_summary = _base.diff_summary


def plan_apply(prefs_path: Path, prefs: dict, raw_table: object) -> Plan:
    """Schema-aware variant of the shared settings ``plan_apply``.

    Enum-name coercion runs *before* the base layer so all downstream
    bookkeeping (diff, state sidecar, verify) sees the on-disk
    representation Vivaldi actually expects.  Validation errors abort
    here because writing them through would silently no-op at runtime
    -- exactly the failure mode the schema layer exists to prevent.
    """
    extra_warnings: list[str] = []
    if isinstance(raw_table, dict):
        schema = _schema.load_schema()
        if schema is not None:
            warnings, errors = _schema.coerce_and_validate(
                raw_table, schema, current_prefs=prefs
            )
            if errors:
                sys.exit(
                    "error: [settings] failed Vivaldi schema validation:\n  "
                    + "\n  ".join(errors)
                )
            extra_warnings = warnings

    plan = _base.plan_apply("vivaldi", prefs_path, prefs, raw_table)
    if extra_warnings:
        plan.warnings.extend(extra_warnings)
    return plan


def cmd_dump(args: argparse.Namespace) -> None:
    _base.cmd_dump("vivaldi", args)


def cmd_blocked(args: argparse.Namespace) -> None:
    _base.cmd_blocked("vivaldi", args)


# --------------------------------------------------------------------------
# Schema-backed inspection commands
# --------------------------------------------------------------------------

def _require_schema() -> dict[str, dict]:
    schema = _schema.load_schema()
    if schema is None:
        sys.exit(
            "error: Vivaldi prefs schema not found.\n"
            "  Looked in standard install paths; set "
            "DOTBROWSER_VIVALDI_PREFS_DEF=/path/to/prefs_definitions.json "
            "to override."
        )
    return schema


def cmd_search(args: argparse.Namespace) -> None:
    schema = _require_schema()
    matches = _schema.search(schema, args.query)
    if not matches:
        sys.exit(f"no settings match query: {args.query!r}")
    out_lines: list[str] = []
    for key, defn in matches[: args.limit]:
        out_lines.extend(_schema.format_def(key, defn))
        out_lines.append("")
    if len(matches) > args.limit:
        out_lines.append(
            f"# ...{len(matches) - args.limit} more matches (use --limit to widen)"
        )
    sys.stdout.write("\n".join(out_lines).rstrip() + "\n")


def cmd_describe(args: argparse.Namespace) -> None:
    schema = _require_schema()
    defn = _schema.lookup(schema, args.key)
    if defn is None:
        sys.exit(
            f"error: key {args.key!r} not found in Vivaldi prefs schema "
            f"(try `dotbrowser vivaldi settings search ...`)"
        )

    lines = _schema.format_def(args.key, defn)

    prefs_path = find_preferences(args.profile_root, args.profile)
    prefs = load_prefs(prefs_path)
    cur = _get_value(prefs, _split_key(args.key))
    if cur is _MISSING:
        lines.append("  current: <not set>")
    else:
        lines.append(f"  current: {json.dumps(cur)}")

    sys.stdout.write("\n".join(lines) + "\n")


def register(subparsers: argparse._SubParsersAction) -> None:
    sub = _base.register("vivaldi", subparsers)

    s = sub.add_parser(
        "search",
        help="search the Vivaldi prefs schema by keyword",
    )
    s.add_argument(
        "query",
        help="case-insensitive query; matches key, description, enum names",
    )
    s.add_argument(
        "--limit",
        type=int,
        default=20,
        help="max matches to print (default: 20)",
    )
    s.set_defaults(func=cmd_search)

    d = sub.add_parser(
        "describe",
        help="show the schema entry for a key plus its current value",
    )
    d.add_argument("key", help="dotted-path key, e.g. vivaldi.tabs.bar.position")
    d.set_defaults(func=cmd_describe)
