# Automation-Flow-from-Wealthsimple-to-YimuBill

**English** | [中文](README.zh-CN.md)

Converts Wealthsimple credit card CSV exports into a format importable by Yimu Bookkeeping (一木记账).

Amounts and dates are filled in by the script. Categories are resolved automatically through mapping rules in a configuration file; only unmatched entries need manual handling.

## Highlights

**From bank account to bookkeeping app.** The issuer's own categorization rarely matches how you actually track spending. This tool maps between them.

**Configurable without touching code.** Unrecognized categories are captured automatically and written back into the TOML config as commented stubs — uncomment and fill in a few characters.

**Per-merchant overrides.** When the issuer assigns a merchant to category A but you want it in category B, one line in `[merchant_map]` handles it permanently.

**No waiting for the monthly statement.** Export Recent Activity whenever you want and import it immediately, instead of once a billing cycle.

## Requirements

Python 3.11+ (uses the standard-library `tomllib`). No third-party dependencies.

## Files

| File | Description |
| --- | --- |
| `ws2yimu.py` | Conversion script |
| `categories.example.toml` | Configuration template |
| `categories.toml` | Your actual configuration; excluded via `.gitignore` |
| `example.csv` | Anonymized Wealthsimple export sample |
| `.gitignore` | Excludes transaction data, actual configuration, and dedup state |

## Usage

```bash
cp categories.example.toml categories.toml
# edit categories.toml
python3 ws2yimu.py aug.example.csv
```

Full workflow:

1. Wealthsimple → Credit card → Recent activity → export CSV
2. Run `ws2yimu.py` to produce `<infile>_yimu.csv`
3. Transfer the output file to your mobile device
4. Yimu → Profile → Import/Export → Excel/CSV bill import → Custom import
5. In Yimu, filter by the sentinel category 「待分类」 and batch-assign

Sample output:

```
读入 19 行,输出 18 行 -> example_yimu.csv

跳过:
    1  信用卡还款

未命中映射(将以待分类进入一木,按需补进 TOML):
    1  category='Services'  merchant='blssmupbill'
    1  category='Medical'  merchant='specsavers bayview gle'
    1  category='Beauty'  merchant="l'amour beauty & life"
```

```
Read 19 rows, wrote 18 rows -> example_yimu.csv

Skipped:
    1  credit card payment

Unmatched (imported under the sentinel category; add to TOML as needed):
    1  category='Services'  merchant='v*blssmupbill'
    1  category='Medical'  merchant='specsavers bayview gle'
    1  category='Beauty'  merchant="l'amour beauty & life"
```

### Command-line options

| Option | Description |
| --- | --- |
| `-o, --outfile` | Output path; defaults to `<infile>_yimu.csv` |
| `-c, --config` | Configuration file path; defaults to `categories.toml` |
| `--sync-toml` | Write unmatched categories back to the config as commented stubs |
| `--no-bom` | Emit output without a UTF-8 BOM |
| `--dedup` | Skip entries already emitted, per the state file; off by default |
| `--state` | Path to the dedup state file |

## Configuration

The configuration file contains three tables:

**`[taxonomy]`** declares the category tree used in Yimu, in the form `"top-level" = ["subcategory", ...]`. It takes no part in conversion; it is used at load time to validate the values in the other two tables. Yimu silently creates any category it does not recognize on import, so this check is the only safeguard against polluting the category tree. Validation failures emit a warning and do not halt execution.

**`[category_map]`** maps a Wealthsimple category to `(top-level, subcategory)`. Exact match.

**`[merchant_map]`** maps a normalized merchant name to `(top-level, subcategory)`. Substring match, and takes precedence over `[category_map]`. Use it to override entries where the issuer's granularity is insufficient or wrong — for instance, it merges fuel and parking into `Gas, parking, and tolls`.

See the template file for worked examples.

## Conversion behavior

**Filtering.** Entries with `status` other than `Completed` are skipped (pending amounts can still change), as are credit card payments (positive amounts that would otherwise be recorded as income).

**Merchant normalization.** Payment-gateway prefixes (`Sq *`, `Priceln*`, `Sg*V*`, which may stack), order IDs, store numbers, and `.ca`/`.com` suffixes are stripped in sequence, then the name is lowercased. `Mcdonalds 40254` and `Mcdonalds 40330` therefore collapse to the same key.

**Category resolution.** `[merchant_map]` is tried first, then `[category_map]`. If neither matches, the top-level category becomes the sentinel value 「待分类」 and the subcategory holds the original Wealthsimple category as a hint.

**Output.** UTF-8 CSV with the header `日期 / 收支类型 / 金额 / 类别 / 子类 / 所属账本 / 收支帐户 / 备注`. The note field preserves the original merchant name.

## Known limitations

- Wealthsimple exports carry no time of day; transactions are accurate to the date only.
- If the subcategory is empty on import, Yimu files the entire row under 「其他」. Categories used at the top level only must be written as `["Grocery", "Grocery"]`.
- The `--dedup` fingerprint is `date|merchant|amount`, relying on occurrence counts to distinguish multiple same-day transactions at the same merchant for the same amount. Reliability is limited.
- Yimu's handling of a UTF-8 BOM and of comma-containing fields (such as `Gas, parking, and tolls`) has not been verified.

## Privacy

Transaction data, actual configuration, and dedup state are all excluded. Check before committing:

```bash
git ls-files | grep -iE '\.csv$|state|\.bak$'
```

Nothing but `example.csv` should appear.

## Acknowledgement

The code and documentation in this repository were produced with the assistance of Claude (Anthropic). Design decisions and verification are the author's.

## License

MIT
