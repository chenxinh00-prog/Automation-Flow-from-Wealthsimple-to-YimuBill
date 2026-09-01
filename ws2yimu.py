#!/usr/bin/env python3
"""
Wealthsimple 信用卡 CSV -> 一木记账「自定义导入」CSV

用法:
    python3 ws2yimu.py credit-card-activities-2026-09-01.csv
    python3 ws2yimu.py in.csv -o out.csv -c categories.toml
    python3 ws2yimu.py in.csv --no-dedup        # 忽略水位线,全量输出

导入方式:一木 -> 个人中心 -> 导入/导出 -> Excel/CSV账单导入 -> 自定义导入
列映射(输出文件的表头已经是中文,一木大概率能自动对上,对不上就手动选):
    一级分类 / 二级分类 / 收支类型 / 金额 / 日期 / 账户 / 备注
"""

import argparse
import csv
import json
import re
import sys
import tomllib
from collections import Counter
from pathlib import Path

# Windows 终端默认代码页(cp936/cp1252)打不出中文和 ⚠,会抛 UnicodeEncodeError。
# 报告是在写完文件之后打印的,崩在这里会导致水位线不落盘,静默破坏去重。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

OUT_FIELDS = ["日期", "收支类型", "金额", "一级分类", "二级分类", "账户", "备注"]

# 支付网关 / 聚合商前缀:Sq *Rooms Coffee、Priceln*Capsule Reside、TST* 等
GATEWAY_PREFIX = re.compile(r"^[a-z0-9]{2,10}\s*\*\s*")
# 域名尾巴 + 订单号:Staples.Ca/48901789128
ORDER_SUFFIX = re.compile(r"/\s*\d{4,}\s*$")
# 门店号:Loblaw #1028、Shell C12579、T&T Supermarket #035
STORE_SUFFIX = re.compile(r"\s+#?[a-z]?\d{3,}\s*$")
# 残留的孤立标点(截断造成的 "! 17 Bal" 之类先不动,交给子串匹配)
MULTISPACE = re.compile(r"\s+")


# ---------------------------------------------------------------- 配置

def load_config(path):
    """读取 TOML。校验只告警,不抛异常、不改数据。"""
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
                print(f"⚠ {name}[{key!r}] 不是 [一级, 二级] 两项", file=sys.stderr)
                continue
            l1, l2 = pair
            if l1 not in tax or l2 not in tax.get(l1, []):
                print(f"⚠ {name}[{key!r}] -> {l1}/{l2} 不在 taxonomy 中", file=sys.stderr)

    # merchant_map 按 key 长度降序,让更具体的 key 先命中
    cfg["_merchant_rules"] = sorted(
        cfg["merchant_map"].items(), key=lambda kv: len(kv[0]), reverse=True
    )
    return cfg


# ---------------------------------------------------------------- 商户

def normalize(merchant):
    """Sq *Rooms Coffee ! 17 Bal -> rooms coffee ! 17 bal"""
    s = (merchant or "").strip().lower()
    s = GATEWAY_PREFIX.sub("", s)
    s = ORDER_SUFFIX.sub("", s)
    s = STORE_SUFFIX.sub("", s)
    s = s.replace(".ca", "").replace(".com", "")
    return MULTISPACE.sub(" ", s).strip()


def resolve(ws_category, norm_merchant, cfg):
    """(一级, 二级)。merchant 子串匹配优先于 category 精确匹配。"""
    for key, pair in cfg["_merchant_rules"]:
        if key in norm_merchant:
            return pair[0], pair[1]
    hit = cfg["category_map"].get(ws_category)
    if hit:
        return hit[0], hit[1]
    sentinel = cfg["settings"].get("sentinel_l1", "待分类")
    return sentinel, (ws_category or "未知")


# ---------------------------------------------------------------- 去重

def fingerprint(row):
    """没有 transaction id,只能用 日期|规范化商户|金额。
    同日同店同额的多笔靠调用方加序号区分。"""
    return f"{row['transaction_date']}|{normalize(row['merchant'])}|{row['amount']}"


def load_state(path):
    if path.exists():
        return Counter(json.loads(path.read_text(encoding="utf-8")))
    return Counter()


def save_state(path, counter):
    path.write_text(json.dumps(dict(counter), ensure_ascii=False, indent=0),
                    encoding="utf-8")


def read_rows(path):
    """WS 目前吐的是纯 ASCII,但商户名里迟早会出现重音符或中文。
    先按 utf-8(带 BOM 容错)读,失败再退到 cp1252。"""
    for enc in ("utf-8-sig", "cp1252"):
        try:
            with open(path, newline="", encoding=enc) as f:
                rows = list(csv.DictReader(f))
            if enc != "utf-8-sig":
                print(f"注意:输入文件不是 UTF-8,已按 {enc} 读取", file=sys.stderr)
            return rows
        except UnicodeDecodeError:
            continue
    raise SystemExit(f"无法解码 {path}:既不是 UTF-8 也不是 cp1252")


# ---------------------------------------------------------------- 主流程

def convert(rows, cfg, seen, use_dedup):
    out, skipped, unmapped = [], Counter(), Counter()
    account = cfg["settings"].get("account", "")
    batch = Counter()

    for row in rows:
        # 1) 只要已入账的。pending 金额会变(小费、外币汇率)
        if row.get("status", "").strip().lower() != "completed":
            skipped["pending / 非 Completed"] += 1
            continue

        # 2) 排除还款。它是正数,不过滤会变成一笔巨额"收入"
        ttype = row.get("transaction_type", "").strip().lower()
        if "payment" in ttype:
            skipped["信用卡还款"] += 1
            continue

        try:
            amount = float(row["amount"])
        except (TypeError, ValueError):
            skipped["金额无法解析"] += 1
            continue
        if amount == 0:
            skipped["零金额"] += 1
            continue

        # 3) 去重:同一指纹出现第 n 次,和历史记录里的次数比对
        fp = fingerprint(row)
        batch[fp] += 1
        if use_dedup and batch[fp] <= seen.get(fp, 0):
            skipped["上次已导入"] += 1
            continue

        # 4) 非 CAD 提示一下,不自动换算
        cur = row.get("currency", "CAD").strip().upper()
        if cur and cur != "CAD":
            skipped[f"外币 {cur}(仍会输出,金额未换算)"] += 1

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
            "一级分类": l1,
            "二级分类": l2,
            "账户": account,
            "备注": note,
        })

    return out, skipped, unmapped, batch


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("infile")
    ap.add_argument("-o", "--outfile")
    ap.add_argument("-c", "--config", default="categories.toml")
    ap.add_argument("--state", default=".ws2yimu_state.json")
    ap.add_argument("--no-dedup", action="store_true")
    ap.add_argument("--no-bom", action="store_true",
                    help="输出不带 UTF-8 BOM。若一木识别不出中文表头,试试这个")
    args = ap.parse_args()

    cfg = load_config(args.config)
    state_path = Path(args.state)
    seen = load_state(state_path)

    rows = read_rows(args.infile)

    out, skipped, unmapped, batch = convert(rows, cfg, seen, not args.no_dedup)

    outfile = args.outfile or str(Path(args.infile).with_suffix("")) + "_yimu.csv"
    # 默认带 BOM(Excel 友好)。一木若因 BOM 认不出首列表头,用 --no-bom
    enc = "utf-8" if args.no_bom else "utf-8-sig"
    with open(outfile, "w", newline="", encoding=enc) as f:
        w = csv.DictWriter(f, fieldnames=OUT_FIELDS)
        w.writeheader()
        w.writerows(out)

    # 先落盘再报告:报告只是打印,不该有机会影响去重状态
    if not args.no_dedup:
        seen.update(batch)
        save_state(state_path, seen)

    # ---- 报告
    print(f"\n读入 {len(rows)} 行,输出 {len(out)} 行 -> {outfile}")
    if skipped:
        print("\n跳过:")
        for reason, n in skipped.most_common():
            print(f"  {n:>3}  {reason}")
    if unmapped:
        print("\n未命中映射(将以待分类进入一木,按需补进 TOML):")
        for (cat, merch), n in unmapped.most_common():
            print(f"  {n:>3}  category={cat!r}  merchant={merch!r}")

    if not args.no_dedup:
        print(f"\n水位线已更新:{state_path}({len(seen)} 条指纹)")


if __name__ == "__main__":
    main()