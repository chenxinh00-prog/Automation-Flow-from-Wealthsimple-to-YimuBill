# Automation-Flow-from-Wealthsimple-to-YimuBill 信用卡最近账单自动化记账

将 Wealthsimple 信用卡导出的 CSV 转换为一木记账可导入的格式。

金额与日期由脚本填充,分类通过配置文件中的映射规则自动解析,仅未命中的条目需要人工处理。

## 主要亮点

- 从银行账户到你的记账软件。银行每笔交易的分类不完全符合记账习惯？这个工具可以完成映射。

- 高度便利的自定义。账单里未识别的类别会被程序自动捕捉并放入 toml，只需要手动改写几个字符。

- 商户自定义。某个交易商户被银行识别为类别A，但你实际上想把它归为类别B？手动添加一行到 toml - Merchant 里就可以解放双手，解放大脑。

- 不需要等到银行每月一次的Statement才导入。随时导出 Recent Activity，随时导入你的软件。更了解自己随时花了多少。

## 环境要求

Python 3.11+(依赖标准库 `tomllib`)。无第三方依赖。

## 文件

| 文件 | 说明 |
| --- | --- |
| `ws2yimu.py` | 转换脚本 |
| `categories.example.toml` | 配置模板 |
| `categories.toml` | 实际配置,已在 `.gitignore` 中排除 |
| `example.csv` | 脱敏的 Wealthsimple 导出样本 |
| `.gitignore` | 排除交易数据、实际配置与去重状态文件 |

## 使用

<!-- cp categories.example.toml categories.toml -->
```bash
# 编辑 categories.toml
python3 ws2yimu.py aug.example.csv
```

完整流程:

1. Wealthsimple → Credit card → Recent activity,导出 CSV
2. 运行 `ws2yimu.py`,得到 `<infile>_yimu.csv`
3. 将输出文件传至移动设备
4. 一木记账 → 个人中心 → 导入/导出 → Excel/CSV 账单导入 → 自定义导入
5. 在一木中筛选哨兵分类「待分类」,批量补全

输出示例:

```
Read 19 rows, wrote 18 rows -> example_yimu.csv

Skipped:
    1  credit card payment

Unmatched (imported under the sentinel category; add to TOML as needed):
    1  category='Services'  merchant='v*blssmupbill'
    1  category='Medical'  merchant='specsavers bayview gle'
    1  category='Beauty'  merchant="l'amour beauty & life"
```

```
读入 19 行,输出 18 行 -> example_yimu.csv

跳过:
    1  信用卡还款

未命中映射(将以待分类进入一木,按需补进 TOML):
    1  category='Services'  merchant='blssmupbill'
    1  category='Medical'  merchant='specsavers bayview gle'
    1  category='Beauty'  merchant="l'amour beauty & life"
```

### 命令行参数

| 参数 | 说明 |
| --- | --- |
| `-o, --outfile` | 输出路径,默认 `<infile>_yimu.csv` |
| `-c, --config` | 配置文件路径,默认 `categories.toml` |
| `--sync-toml` | 将未命中的 category 以注释形式写回配置文件 |
| `--no-bom` | 输出不含 UTF-8 BOM |
| `--dedup` | 依据状态文件跳过已输出的条目,默认关闭 |
| `--state` | 去重状态文件路径 |

## 配置

配置文件包含三张表:

**`[taxonomy]`** 声明一木中的分类树,格式为 `"一级分类" = ["二级分类", ...]`。该表不参与转换,仅在加载时用于校验另外两张表的取值。一木在导入时遇到不存在的分类会静默创建,因此此处校验是防止分类树被污染的唯一环节。校验失败仅输出告警,不中止执行。

**`[category_map]`** 将 Wealthsimple 的 category 映射为 `(一级分类, 二级分类)`,精确匹配。

**`[merchant_map]`** 将规范化后的商户名映射为 `(一级分类, 二级分类)`,子串匹配,优先级高于 `[category_map]`。用于覆盖 Wealthsimple 归类粒度不足或有误的条目,例如其将加油与停车合并为 `Gas, parking, and tolls`。

详细示例请参考文件。

## 转换行为

**过滤**  跳过 `status` 非 `Completed` 的条目(pending 交易金额可能变动)与信用卡还款(金额为正,不过滤将计为收入)。

**商户名规范化**  依次剥离支付网关前缀(`Sq *`、`Priceln*`、`Sg*V*` 等,可叠加)、订单号、门店号及 `.ca`/`.com` 后缀,转为小写。`Mcdonalds 40254` 与 `Mcdonalds 40330` 因此归一为同一键。

**分类解析**  优先匹配 `[merchant_map]`,其次 `[category_map]`;均未命中时一级分类取哨兵值「待分类」,二级分类取 Wealthsimple 的原始 category 以保留线索。

**输出**  UTF-8 CSV,表头为 `日期 / 收支类型 / 金额 / 类别 / 子类 / 所属账本 / 收支帐户 / 备注`。备注字段保留原始商户名。

## 已知限制

- Wealthsimple 的导出不含时刻,交易时间精度为天。
- 一木在导入时若二级分类为空,会将整行归入「其他」。仅使用一级分类的类目需写作 `["Grocery", "Grocery"]`。
- `--dedup` 的指纹为 `日期|商户|金额`,依赖出现次数区分同日同商户同金额的多笔交易,可靠性有限。
- 一木对 UTF-8 BOM 及含逗号字段(如 `Gas, parking, and tolls`)的解析行为未经验证。

<!-- TODO: 使用一段时间后补充 -->

## 隐私

交易数据、实际配置与去重状态均已排除。提交前检查:

```bash
git ls-files | grep -iE '\.csv$|state|\.bak$'
```

除 `example.csv` 外应无输出。

## 声明

本仓库的代码与文档在 Claude(Anthropic) 辅助下产出,设计决策与测试验证由作者完成。

## License

MIT
