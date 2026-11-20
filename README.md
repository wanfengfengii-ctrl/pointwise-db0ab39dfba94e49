# 河湾计量报送仓 V1

河湾污水处理厂用这套仓库把值班台的有序计量批交给独立监管接收仓。接入员登记受管出口、接纳日批并提交更正，计量员按指定修订核定，报送员发行固定件，监管接收员暂存和入账，监管值班员在源仓停开时按报送号核账。

## 运行

在项目根目录使用 Python 3.11。运行环境只需标准库。控制台在项目物化时提供 `check_project.py`。

```text
python3 -B check_project.py
python3 -B -m unittest discover -s tests -t . -v
python3 -B -m demo
python3 -B -m shared_examples
```

演示从 `samples/day.json` 与 `samples/shared_day.json` 读取样例，在临时目录创建两个 SQLite 文件。白班首版 A 5、A 7、B 3 mL 核定为 15 mL，将 7 改为 8 的版本另存为 16 mL。R6 发行首版，夜班的 R7 发行 A 5、A 7 mL。暂存时账额不增加。入账、确认和重投后，关闭源仓与接收仓，再单独重开接收仓核对原序明细和原回执。共表 `forced` 件 S1 在源仓关闭后入账，正式明细为第 1 段 5 mL 去 B、第 2 段 5 mL 去 A（两行 10 mL），再重开监管仓核出段去向与原回执。固定预期与实测不符时演示以非零状态退出。`shared_examples` 用普通 V1 报送展示同一组行的调序，V1 汇总仍相同，只运行现有业务。

## 初始化与日常交接

`intake.source_store.open_source(path)` 与 `posting.receiver_store.open_receiver(path)` 打开两个不同的 SQLite 文件，由调用者关闭连接。新源仓依次调用 `initialize_intake`、`closeout.results.initialize_closeout`、`dispatch.outbox.initialize_outbox`。接收仓调用 `initialize_receiver`。初始化补齐各自的表和索引，保留用户登记的出口、各版批料、固定核定、发行原件、确认、暂存和正式账。仓库写入口管理自己的事务，调用时使用无活动事务的连接。每个并行调用方使用自己的连接。

| 操作 | 调用与返回 |
| --- | --- |
| 登记出口 | `intake.outlets.register_outlet(source, plant, outlet)` 幂等登记，`find_outlet` 查该厂区关系。 |
| 接纳批料 | `intake.batches.accept_batch(source, plant, day, batch, rows, parent_id=None)` 返回修订号。`intake.revisions.get_revision` 按号读取完整修订。已登记的日批更正带同批父号，一次提交完整有序行。 |
| 接纳共表批 | `intake.batches.accept_shared_batch(source, plant, day, batch, slots, bounds, parent_id=None)` 返回与普通日批同一编号空间的修订号。`slots` 为有序段：每段 `ml` 为非负整数、`outlets` 为许可出口；`bounds` 为出口到 `{'min_ml','max_ml'}` 的闭合日界。`intake.revisions.get_shared_revision` 读回时段与日界。 |
| 核定 | `closeout.jobs.close_batch(source, revision_id)` 返回 `CloseoutResult`，包含 `result_id`、固定修订和汇总。共表修订额外带 `shared`（按时段固定的整 mL 分摊）。无解时抛 `ValueError`，保留修订且无核定结果。`closeout.results.get_result` 按结果号重领。 |
| 发行 | `dispatch.issuance.issue_report(source, report_id, result_id)` 返回 `IssuedReport`。同号同结果重办及 `dispatch.outbox.get_issued_report` 返回原件与当前交班状态。共表结果使用 `version` 为 2 的正文。 |
| 暂存 | `posting.staging.stage_report(receiver, issued.envelope)` 独立验件并返回整数暂存号。相同原件复用该号，`get_staged_report` 可读固定字节。 |
| 入账 | `posting.ledger.post_report(receiver, stage_id)` 返回 `PostedReport` 的正式行、固定汇总和原回执。重办及 `posting.receipts.get_receipt` 可补领同一回执。`get_ledger_totals` 从正式明细统计全仓或指定报送。 |
| 确认 | `dispatch.acknowledgments.confirm_receipt(source, report_id, posted.receipt.to_payload())` 持久保存匹配回执。丢失请求或回包时，发行仍可重领，交班仍待确认。 |
| 核账 | `review.reports.audit_report(receiver, report_id)` 按正式报送号返回接收原序行、监管账行、两套固定及重算汇总、原回执和差异。共表件另带 `slot_allocations`：按原段顺序的 `(段位, 原段量, ((账行序号, 出口, 分配量), ...))`，零读数段附空去向。 |

## 共表日批（V2）

共表日批在核定时才确定每段去哪个出口。接纳用 `accept_shared_batch`，日批、日号、批号及同批父修订规则与普通日批相同，但每次接纳都提供该修订的完整时段和日界，不从父修订继承。`slots` 的身份按输入位置从 1 计数，等量时段分别保留；每段 `ml` 为非负 Python 整数，`outlets` 去重后升序，可为空。`bounds` 覆盖所有许可出口，日界闭合且 `0 <= min_ml <= max_ml`；未获分配的出口按 0 mL 核对，空批仍核对全部日界。

核定把每段读数整 mL 分给本段许可的出口，逐段守恒，所有时段分给同一出口的总量落在该出口日界内。这是带下界的二部运输可行性问题，`closeout.shared.solve_allocation` 用最小费用无关的环流求解，任取一个可行解并冻结。存在多个解时后续请求读取同一冻结选择。正式明细按时段位置、出口名排序，每个正分配产生一行并连续编号，零分配不计行，同段同出口归为一行。批料非法抛 `ValueError` 且不新增修订；合法但无解时核定抛 `ValueError`，保留修订，无核定结果或可发行件。

V2 正文在 V1 身份（报送号、结果号、厂区、日号、批号、修订号）和汇总含义之上，携带完整有序 `slots`、`bounds` 以及可独立核验的 `allocations`（按段位、出口排序的正分配，形如 `{'ordinal','outlet','ml'}`），并以 `rows`／`outlets`／`total` 声明投影明细。`rows` 是 `allocations` 按段位、出口排序后连续编号的投影；接收端用证据重新推导投影，只有当声明的 `rows`／`outlets`／`total`（如有）与投影逐行一致、且每段守恒、每段只用许可出口、每个出口落入日界、摘要覆盖完整正文时，才生成暂存。任一合法分摊都可接纳；正文不强制包含声明字段，但出现即须与投影一致。

## 数据规则与失败边界

厂区、日号、批号、报送号及出口是非空白 UTF-8 文本标识。批料为有序序列，每行只含 `outlet`、`ml`，`ml` 必须是 Python 整数。空值、布尔、浮点、缺字段和未知出口整批拒绝。允许整数的正、负和零值。序号从 1 起，以到达顺序保留每次出现。出口汇总按标识排序，整批按所选日批修订计数并求和，空批有明确的零汇总。数量在数据库及线上正文使用规范十进制文本，经 Python 整数精确计算。

`ReportEnvelope` 携带 `report_id`、`original_id` 及 `body`。身份为原始正文 UTF-8 字节的 SHA-256 十六进制摘要。V1 JSON 固定字段为 `version`、`report_id`、`result_id`、`plant`、`day`、`batch`、`revision_id`、`rows`、`outlets` 和 `total`。原序行使用 `ordinal`、`outlet`、`ml`。出口汇总使用 `outlet`、`row_count`、`ml`。整批汇总使用 `row_count`、`ml`。接收时检查字节身份、必需字段、重复字段、原序和逐项声明。源仓交班的 V1 回执字段为 `receipt_id`、`report_id`、`original_id`。V2 共表件沿用同一套回执与暂存规则，只是正文改为上文的结构；V1 件仍按原字节、接口、回执和核账行为处理。

**时段与正式行的对应关系。** V2 的 `allocations` 逐项是 `(段位 ordinal, 出口 outlet, 整 mL)`，按段位再按出口名升序。正式明细正是它的投影：从第 1 段到第 N 段，每段按出口名顺序把每个正分配写成下一个连续 `ordinal` 的账行；零分配不写行，零读数段于是没有账行。因此 `slot_allocations` 的 `(账行序号, 出口, 分配量)` 与 `ledger_rows` 的 `ordinal`／`outlet`／`ml` 一一对齐，`forced` 样例的第 1 段 5 mL 给 B 是账行 1，第 2 段 5 mL 给 A 是账行 2。

坏件、异绑定、无效父修订或参数抛出 `ValueError`。核定、发行、入账、确认和核账缺少必需记录时抛出 `KeyError`，用于查询的 `get_*` 可对未知记录返回 `None`。暂存独立提交。正式索引、明细、固定汇总和原回执在一个接收事务内提交，SQLite 失败回滚本次正式写入，原暂存号可以重办。并发重办在接收仓写事务中检查原正式记录。核账使用接收仓同一读快照，按序号报告漏行、多行和错行。汇总差异的 `expected` 是对应的参考值，`actual` 是受核对的读数或重算值。
