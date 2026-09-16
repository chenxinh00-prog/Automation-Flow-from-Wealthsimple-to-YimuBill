#!/usr/bin/env python3
"""
Wealthsimple credit card CSV -> Yimu Bookkeeping "custom import" CSV.

Usage:
    python3 ws2yimu.py credit-card-activities-2026-09-01.csv
    python3 ws2yimu.py in.csv -o out.csv -c categories.toml
    python3 ws2yimu.py in.csv --dedup

Import path in Yimu:
    Profile -> Import/Export -> Excel/CSV bill import -> Custom import

The output header is already in Chinese, so Yimu will usually auto-detect the
columns; if it does not, map them by hand:
    日期 / 收支类型 / 金额 / 类别 / 子类 / 所属账本 / 收支帐户 / 备注
"""

import argparse
import csv
import json
import re
import sys
import tomllib
from collections import Counter
from pathlib import Path

# Windows consoles default to a non-UTF-8 code page (cp936/cp1252) and raise
# UnicodeEncodeError when printing Chinese or the warning glyph. The report is
# printed after the output file is written, so a crash here would leave the
# dedup state unsaved and silently cause duplicate imports next run.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# Column names required by Yimu's custom import; these are data, not prose.
OUT_FIELDS = ["日期", "收支类型", "金额", "类别", "子类", "所属账本", "收支帐户", "备注"]

# Payment gateway / aggregator prefix: "Sq *Rooms Coffee", "Priceln*Capsule
# Reside", "TST*..." and friends.
GATEWAY_PREFIX = re.compile(r"^[a-z0-9]{2,10}\s*\*\s*")
# Domain tail plus order id: "Staples.Ca/48901789128".
ORDER_SUFFIX = re.compile(r"/\s*\d{4,}\s*$")
# Store number: "Loblaw #1028", "Shell C12579", "T&T Supermarket #035".
STORE_SUFFIX = re.compile(r"\s+#?[a-z]?\d{3,}\s*$")
# Leftover punctuation from truncated names ("! 17 Bal") is deliberately kept;
# substring matching in resolve() tolerates it.
MULTISPACE = re.compile(r"\s+")


# ---------------------------------------------------------------- Config

def load_config(path):
    """Read the TOML config. Validation warns only; it never raises."""
    with open(path, "rb") as f:
        cfg = tomllib.load(f)

    cfg.setdefault("settings", {})
    cfg.setdefault("taxonomy", {})
    cfg.setdefault("category_map", {})
    cfg.setdefault("merchant_map", {})

    tax = cfg["taxonomy"]
    for name in ("category_map", "merchant_map"):
        for key, pair in cfg[name].items():
            if not (isinstance(pair, list) and len(pair) == 2):
                print(f"⚠ {name}[{key!r}] is not a [top-level, subcategory] pair",
                      file=sys.stderr)
                continue
            l1, l2 = pair
            if l1 not in tax or l2 not in tax.get(l1, []):
                print(f"⚠ {name}[{key!r}] -> {l1}/{l2} not in taxonomy",
                      file=sys.stderr)

    # Longest key first, so the more specific merchant rule wins.
    cfg["_merchant_rules"] = sorted(
        cfg["merchant_map"].items(), key=lambda kv: len(kv[0]), reverse=True
    )
    return cfg


# ---------------------------------------------------------------- Merchants

def normalize(merchant):
    """Reduce a raw merchant string to a stable lookup key.

    "Sq *Rooms Coffee ! 17 Bal" -> "rooms coffee ! 17 bal"
    """
    s = (merchant or "").strip().lower()
    s = GATEWAY_PREFIX.sub("", s)
    s = ORDER_SUFFIX.sub("", s)
    s = STORE_SUFFIX.sub("", s)
    s = s.replace(".ca", "").replace(".com", "")
    return MULTISPACE.sub(" ", s).strip()


def resolve(ws_category, norm_merchant, cfg):
    """Return (top-level, subcategory).

    merchant_map is matched by substring and takes precedence over
    category_map, which is matched exactly. If neither hits, fall back to the
    sentinel category and keep the raw Wealthsimple category as a hint.
    """
    for key, pair in cfg["_merchant_rules"]:
        if key in norm_merchant:
            return pair[0], pair[1]
    hit = cfg["category_map"].get(ws_category)
    if hit:
        return hit[0], hit[1]
    sentinel = cfg["settings"].get("sentinel_l1", "待分类")
    return sentinel, (ws_category or "未知")


# ---------------------------------------------------------------- Dedup

def fingerprint(row):
    """Identify a row by date|normalized merchant|amount.

    The export carries no transaction id, so multiple same-day transactions at
    the same merchant for the same amount are distinguished by the caller
    counting occurrences.
    """
    return f"{row['transaction_date']}|{normalize(row['merchant'])}|{row['amount']}"


def load_state(path):
    if path.exists():
        return Counter(json.loads(path.read_text(encoding="utf-8")))
    return Counter()


def save_state(path, counter):
    path.write_text(json.dumps(dict(counter), ensure_ascii=False, indent=0),
                    encoding="utf-8")


def read_rows(path):
    """Read the input CSV, falling back to cp1252 if it is not UTF-8.

    Wealthsimple currently emits pure ASCII, but merchant names will eventually
    contain accented or non-Latin characters.
    """
    for enc in ("utf-8-sig", "cp1252"):
        try:
            with open(path, newline="", encoding=enc) as f:
                rows = list(csv.DictReader(f))
            if enc != "utf-8-sig":
                print(f"Note: input is not UTF-8, read as {enc}", file=sys.stderr)
            return rows
        except UnicodeDecodeError:
            continue
    raise SystemExit(f"Cannot decode {path}: neither UTF-8 nor cp1252")


# ---------------------------------------------------------------- TOML write-back

def sync_toml(path, unmapped, cfg):
    """Append unmatched Wealthsimple categories to [category_map] as comments.

    Only category_map is touched: an unmatched row is by definition a category
    that has no mapping. merchant_map exists for the subjective case where
    Wealthsimple's category is correct but undesirable, which the script cannot
    detect.

    Stubs are written commented out rather than as placeholder values. A
    placeholder would be treated as a valid mapping by resolve() and would
    create a real junk category in Yimu, defeating the sentinel mechanism.
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8")

    # Group merchants under their category to use as hints in the comment.
    examples = {}
    for (cat, merch), n in unmapped.items():
        if cat:
            examples.setdefault(cat, []).append(merch)

    todo = [c for c in sorted(examples)
            if c not in cfg["category_map"] and f'"{c}"' not in text]
    if not todo:
        return 0

    block = ["", "# --- added by --sync-toml; uncomment and fill in ---"]
    for cat in todo:
        ex = ", ".join(sorted(set(examples[cat]))[:3])
        block.append(f'# e.g. {ex}')
        block.append(f'# "{cat}" = ["", ""]')

    lines = text.splitlines()
    try:  # Insert just after the [category_map] header.
        i = lines.index("[category_map]") + 1
    except ValueError:  # No such table: append the whole block at end of file.
        i = len(lines)
        block.insert(0, "[category_map]")
    lines[i:i] = block

    backup = path.with_suffix(path.suffix + ".bak")
    backup.write_text(text, encoding="utf-8")
    new = "\n".join(lines) + "\n"
    path.write_text(new, encoding="utf-8")

    try:  # Roll back on a broken write; a convenience feature must not
          # corrupt the config file.
        tomllib.loads(new)
    except tomllib.TOMLDecodeError as e:
        path.write_text(text, encoding="utf-8")
        print(f"Write-back produced invalid TOML, rolled back: {e}",
              file=sys.stderr)
        return 0
    return len(todo)


# ---------------------------------------------------------------- Main pipeline

def convert(rows, cfg, seen, use_dedup):
    out, skipped, unmapped = [], Counter(), Counter()
    account = cfg["settings"].get("account", "")       # -> 所属账本
    dataFrom = cfg["settings"].get("dataFrom", "")     # -> 收支帐户
    batch = Counter()

    for row in rows:
        # 1) Posted transactions only; pending amounts still change (tips, FX).
        if row.get("status", "").strip().lower() != "completed":
            skipped["pending / not Completed"] += 1
            continue

        # 2) Drop credit card payments. They are positive and would otherwise
        #    be recorded as a large income entry.
        ttype = row.get("transaction_type", "").strip().lower()
        if "payment" in ttype:
            skipped["credit card payment"] += 1
            continue

        try:
            amount = float(row["amount"])
        except (TypeError, ValueError):
            skipped["unparseable amount"] += 1
            continue
        if amount == 0:
            skipped["zero amount"] += 1
            continue

        # 3) Dedup: compare this fingerprint's nth occurrence against history.
        fp = fingerprint(row)
        batch[fp] += 1
        if use_dedup and batch[fp] <= seen.get(fp, 0):
            skipped["already imported"] += 1
            continue

        # 4) Flag non-CAD rows; amounts are not converted.
        cur = row.get("currency", "CAD").strip().upper()
        if cur and cur != "CAD":
            skipped[f"foreign currency {cur} (emitted, not converted)"] += 1

        norm = normalize(row["merchant"])
        l1, l2 = resolve(row.get("category", "").strip(), norm, cfg)
        if l1 == cfg["settings"].get("sentinel_l1", "待分类"):
            unmapped[(row.get("category", ""), norm)] += 1

        note = row["merchant"].strip()
        if row.get("notes", "").strip():
            note = f"{note} / {row['notes'].strip()}"

        out.append({
            "日期": row["transaction_date"].strip(),
            "收支类型": "支出" if amount < 0 else "收入",
            "金额": f"{abs(amount):.2f}",
            "类别": l1,
            "子类": l2,
            "所属账本": account,
            "收支帐户": dataFrom,
            "备注": note
        })

    return out, skipped, unmapped, batch


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("infile")
    ap.add_argument("-o", "--outfile")
    ap.add_argument("-c", "--config", default="categories.toml")
    ap.add_argument("--state", default=".ws2yimu_state.json")
    ap.add_argument("--dedup", action="store_true",
                    help="Skip rows already emitted, per the state file. Yimu "
                         "dedups on import, so this is off by default: editing "
                         "the TOML and regenerating the same batch should not "
                         "be blocked.")
    ap.add_argument("--sync-toml", action="store_true",
                    help="Append unmatched Wealthsimple categories to "
                         "categories.toml as commented stubs")
    ap.add_argument("--no-bom", action="store_true",
                    help="Emit output without a UTF-8 BOM. Try this if Yimu "
                         "fails to detect the Chinese header.")
    args = ap.parse_args()

    cfg = load_config(args.config)
    state_path = Path(args.state)
    seen = load_state(state_path) if args.dedup else Counter()

    rows = read_rows(args.infile)

    out, skipped, unmapped, batch = convert(rows, cfg, seen, args.dedup)

    outfile = args.outfile or str(Path(args.infile).with_suffix("")) + "_yimu.csv"
    # BOM by default for Excel. If Yimu cannot detect the first column because
    # of it, use --no-bom.
    enc = "utf-8" if args.no_bom else "utf-8-sig"
    with open(outfile, "w", newline="", encoding=enc) as f:
        w = csv.DictWriter(f, fieldnames=OUT_FIELDS)
        w.writeheader()
        w.writerows(out)

    # Persist before reporting: printing must never affect the dedup state.
    if args.dedup:
        seen.update(batch)
        save_state(state_path, seen)

    # ---- Report
    print(f"\nRead {len(rows)} rows, wrote {len(out)} rows -> {outfile}")
    if skipped:
        print("\nSkipped:")
        for reason, n in skipped.most_common():
            print(f"  {n:>3}  {reason}")
    if unmapped:
        print("\nUnmatched (imported under the sentinel category; add to TOML "
                "as needed):")
        for (cat, merch), n in unmapped.most_common():
            print(f"  {n:>3}  category={cat!r}  merchant={merch!r}")
        if args.sync_toml:
            n = sync_toml(args.config, unmapped, cfg)
            if n:
                print(f"\nAppended {n} commented stubs to [category_map] in "
                        f"{args.config} (backup: {args.config}.bak)")
            else:
                print("\nNo new categories to append")

    if args.dedup:
        print(f"\nState updated: {state_path} ({len(seen)} fingerprints)")


if __name__ == "__main__":
    main()