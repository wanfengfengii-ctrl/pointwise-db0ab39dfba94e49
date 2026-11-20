# 河湾计量报送仓 V1

河湾污水处理厂用这套仓库把值班台的有序计量批交给独立监管接收仓。接入员登记受管出口、接纳日批并提交更正，计量员按指定修订核定，报送员发行固定件，监管接收员暂存和入账，监管值班员在源仓停开时按报送号核账。

## 运行

在项目根目录使用 Python 3.11。运行环境只需标准库。控制台在项目物化时提供 `check_project.py`。

```text
python3 -B check_project.py
python3 -B -m unittest discover -s tests -t . -v
python3 -B -m demo
```

演示从 `samples/day.json` 读取样例，在临时目录创建两个 SQLite 文件。白班首版 A 5、A 7、B 3 mL 核定为 15 mL，将 7 改为 8 的版本另存为 16 mL。R6 发行首版，夜班的 R7 发行 A 5、A 7 mL。暂存时账额不增加。入账、确认和重投后，关闭源仓与接收仓，再单独重开接收仓核对原序明细和原回执。固定预期与实测不符时演示以非零状态退出。

## 初始化与日常交接

`intake.source_store.open_source(path)` 与 `posting.receiver_store.open_receiver(path)` 打开两个不同的 SQLite 文件，由调用者关闭连接。新源仓依次调用 `initialize_intake`、`closeout.results.initialize_closeout`、`dispatch.outbox.initialize_outbox`。接收仓调用 `initialize_receiver`。初始化补齐各自的表和索引，保留用户登记的出口、各版批料、固定核定、发行原件、确认、暂存和正式账。仓库写入口管理自己的事务，调用时使用无活动事务的连接。每个并行调用方使用自己的连接。

| 操作 | 调用与返回 |
| --- | --- |
| 登记出口 | `intake.outlets.register_outlet(source, plant, outlet)` 幂等登记，`find_outlet` 查该厂区关系。 |
| 接纳批料 | `intake.batches.accept_batch(source, plant, day, batch, rows, parent_id=None)` 返回修订号。`intake.revisions.get_revision` 按号读取完整修订。已登记的日批更正带同批父号，一次提交完整有序行。 |
| 核定 | `closeout.jobs.close_batch(source, revision_id)` 返回 `CloseoutResult`，包含 `result_id`、固定修订和汇总。`closeout.results.get_result` 按结果号重领。 |
| 发行 | `dispatch.issuance.issue_report(source, report_id, result_id)` 返回 `IssuedReport`。同号同结果重办及 `dispatch.outbox.get_issued_report` 返回原件与当前交班状态。 |
| 暂存 | `posting.staging.stage_report(receiver, issued.envelope)` 独立验件并返回整数暂存号。相同原件复用该号，`get_staged_report` 可读固定字节。 |
| 入账 | `posting.ledger.post_report(receiver, stage_id)` 返回 `PostedReport` 的正式行、固定汇总和原回执。重办及 `posting.receipts.get_receipt` 可补领同一回执。`get_ledger_totals` 从正式明细统计全仓或指定报送。 |
| 确认 | `dispatch.acknowledgments.confirm_receipt(source, report_id, posted.receipt.to_payload())` 持久保存匹配回执。丢失请求或回包时，发行仍可重领，交班仍待确认。 |
| 核账 | `review.reports.audit_report(receiver, report_id)` 按正式报送号返回接收原序行、监管账行、两套固定及重算汇总、原回执和差异。 |

## 数据规则与失败边界

厂区、日号、批号、报送号及出口是非空白 UTF-8 文本标识。批料为有序序列，每行只含 `outlet`、`ml`，`ml` 必须是 Python 整数。空值、布尔、浮点、缺字段和未知出口整批拒绝。允许整数的正、负和零值。序号从 1 起，以到达顺序保留每次出现。出口汇总按标识排序，整批按所选日批修订计数并求和，空批有明确的零汇总。数量在数据库及线上正文使用规范十进制文本，经 Python 整数精确计算。

`ReportEnvelope` 携带 `report_id`、`original_id` 及 `body`。身份为原始正文 UTF-8 字节的 SHA-256 十六进制摘要。V1 JSON 固定字段为 `version`、`report_id`、`result_id`、`plant`、`day`、`batch`、`revision_id`、`rows`、`outlets` 和 `total`。原序行使用 `ordinal`、`outlet`、`ml`。出口汇总使用 `outlet`、`row_count`、`ml`。整批汇总使用 `row_count`、`ml`。接收时检查字节身份、必需字段、重复字段、原序和逐项声明。源仓交班的 V1 回执字段为 `receipt_id`、`report_id`、`original_id`。

坏件、异绑定、无效父修订或参数抛出 `ValueError`。核定、发行、入账、确认和核账缺少必需记录时抛出 `KeyError`，用于查询的 `get_*` 可对未知记录返回 `None`。暂存独立提交。正式索引、明细、固定汇总和原回执在一个接收事务内提交，SQLite 失败回滚本次正式写入，原暂存号可以重办。并发重办在接收仓写事务中检查原正式记录。核账使用接收仓同一读快照，按序号报告漏行、多行和错行。汇总差异的 `expected` 是对应的参考值，`actual` 是受核对的读数或重算值。
