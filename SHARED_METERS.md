# 共表日批合同

河湾计量报送仓的普通日批在接纳时就有出口归属。共表日批在核定时才确定各段的出口去向。监管仓收到固定报送后，在源仓关闭的情况下独立验件、入账和核账。初始化及原流程见 README.md。

## 接纳与核定

新增 `intake.batches.accept_shared_batch(source, plant, day, batch, slots, bounds, parent_id=None)`。返回与普通日批共用编号空间的整数修订号。沿用厂区、日号、批号及同批父修订规则。每次接纳提供该修订的完整批料和日界，不从父修订继承时段或限制。调用沿用现有源仓初始化及事务约定。

`slots` 是有序序列，每项为含 `ml` 和 `outlets` 的映射。`ml` 是非负 Python 整数，布尔值无效。`outlets` 为不重复的出口名序列，序列内部的顺序不影响许可。可给空序列。时段身份按输入位置从 1 计数，等量时段分别保留。`bounds` 是出口名到 `{'min_ml': int, 'max_ml': int}` 的映射，日界闭合，要求 `0 <= min_ml <= max_ml`。所有列入 `bounds` 的出口都须在该厂区登记，每个时段许可的出口也都须出现在 `bounds`。未获得分配的出口按 0 mL 核对日界。空批仍核对全部日界。

每段分配必须为非负整数，且只能流向本段许可的出口。每段分配之和等于该段读数，所有时段分给同一出口的总量须在该出口日界内。`closeout.jobs.close_batch(source, revision_id)` 核定一份可行分摊，重办及 `closeout.results.get_result` 重领固定结果。存在多个解时任选其一，后续请求读取已核定的选择。结果仍通过 `result_id`、`revision.rows` 和 `totals` 提供身份、账行和汇总。正式明细按时段位置和出口名排序，每个正分配产生一行并连续编号，零分配不计行。同段同出口的量归为同一正项。非法批料抛 `ValueError` 且不新增修订。批料合法但无解时，核定抛 `ValueError`，保留接纳的修订，无核定结果或可发行件。

## 固定报送、入账与核账

沿用 `issue_report(source, report_id, result_id)`、`get_issued_report` 及 `ReportEnvelope`。共表结果使用 `version` 为 2 的 UTF-8 JSON 正文，保留报送号、固定核定号、厂区、日号、批号、修订号和原有汇总含义。正文携带该修订的完整有序时段、许可、出口日界和可独立核验的实际分摊证据。证据布局由本次实现选定，在交付说明中写清含义及与账行的对应关系。线上 mL 沿用 V1 的规范十进制文本，摘要覆盖完整正文原字节。同号同结果重领原件，同号异结果拒绝。后续修订和重开不改变已经签发的原件。

监管仓必须能在源仓关闭后，从原件独立核验各时段的分摊，并且只把这份分摊投影出的明细正式入账。报送号尚未占用时，任一满足固定约束且与实际入账明细一致的分摊都可接纳。若正文另带账行声明，声明必须与证据投影逐行一致。须核对原件身份、完整结构、时段归属、时段守恒、出口许可、日界与逐项汇总，摘要正确的业务坏件仍须拒绝。

接收入口沿用 `stage_report(receiver, envelope)` 和 `post_report(receiver, stage_id)`。坏件在暂存时抛 `ValueError`，无新暂存、正式账或回执。暂存不计量。沿用同件重投、同号异件拒绝和回执匹配规则。正式账、固定汇总和回执一起提交，SQLite 提交失败保留原暂存供重试。`get_posted_report`、`get_ledger_totals`、`get_receipt` 和 `confirm_receipt` 沿原接口消费正式成果。

`review.reports.audit_report(receiver, report_id)` 对新版保留原核账字段，并提供 `slot_allocations`。它是按原段顺序返回的元组，每项形如 `(段位, 原段量, ((账行序号, 出口, 分配量), ...))`，数量为整数。零读数段带空去向。原件行及监管账行仍按 V1 的 `ordinal`、`outlet` 和 `ml` 核对，段去向体现经验证的原件归属。关闭源仓及重开监管仓后仍可按原号读取。普通 V1 件沿用原字节、接口、回执和核账行为。

## 样例和交付核验

`samples/shared_day.json` 提供接纳样例和手算核账结果。`expected` 使用 `slot_allocations` 的数组写法，`null` 表示接纳后核定无解。`examples` 列出同一输入的两个合法选法。`forced` 的正确结果是先 B 5、后 A 5，正式账为两行 10 mL。`python3 -B -m shared_examples` 用普通 V1 报送展示这一组行调序后，V1 的汇总仍相同。该对照只运行现有业务。

完成后沿 `.pointwise-runtime.json` 的 `verify` 和 `demo` 入口验收，保留原回归和原演示。核验应覆盖无解不核定、逐段归属错而总量正确的拒收、合法多解、等量重复段和零段、重试及断源重开。核验失败以非零状态退出。演示 `forced` 件在关闭源仓后入账，再重开监管仓核对去向与原回执。可以用 `tests/support.py` 的 `edit_body(envelope, edit)` 为业务错件重算摘要，并在未占用该号的接收仓验件。提交失败可用 `open_receiver(path, factory=CommitFailureConnection)` 打开连接，暂存后以 `receiver.arm(observe)` 触发下一次正式提交的延迟外键失败，然后按原暂存号重试。
