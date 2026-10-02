# 客服工单工作台

记录客户问题、指派处理人、添加处理备注，再以明确解决说明关闭工单。

## 运行

需要 Python 3.10 或更新版本，仅使用标准库，无依赖安装步骤。请在本目录运行：

```sh
python3 -m support_desk --root ./state demo
python3 -m unittest discover -s tests -v
```

这是本地命令行程序，不监听网络端口，无账户或密码。`demo` 在指定 root 的临时子目录中读取 examples 样例并演示业务，结束后清理样例状态，不改变现有数据。

## 正常使用

公开 API：`from support_desk import SupportDesk`，然后 `SupportDesk(root)`。每个命令接收可选的 JSON 文件，其对象键与 API 方法参数一致。例如：

```sh
python3 -m support_desk --root ./state open examples/tickets.json
```

JSON 数组会按顺序执行多个独立操作；先前成功操作保留，后续失败不会回滚整批。重跑登记命令遇到已存在的标识会报错。

- `open` → `SupportDesk.open_ticket(...)`：可选 `opened_at`，非负整数分钟；提供时工单同时带初值为 `null` 的 `first_response`，省略时不写这两个字段。参数名见 `core.py` 的公开方法签名。
- `get` → `SupportDesk.get(...)`。参数名见 `core.py` 的公开方法签名。
- `assign` → `SupportDesk.assign(...)`。参数名见 `core.py` 的公开方法签名。
- `note` → `SupportDesk.note(...)`。参数名见 `core.py` 的公开方法签名。
- `close` → `SupportDesk.close(...)`。参数名见 `core.py` 的公开方法签名。
- `list` → `SupportDesk.list_tickets(...)`。参数名见 `core.py` 的公开方法签名。
- `respond` → `SupportDesk.respond(...)`：登记首次响应，参数为 `ticket_id`、`message`、`responded_at`（非负整数分钟，不得早于 `opened_at`，相等允许），返回完整工单；不改变状态、指派、备注或解决说明。记录只有 `message` 与 `responded_at`，不含 `knowledge`。
- `priority-set` → `SupportDesk.set_priority(ticket_id, priority)`：设置工单优先级，`priority` 仅限 `low`、`normal`、`high`、`urgent`。两个参数均须为非空字符串，先去除首尾空白再区分大小写匹配；只允许针对存在且未关闭的工单，成功返回完整工单并写入 `priority` 字段。重复设置相同值同样成功，不追加备注，不改变状态、指派、解决说明或首次响应记录。参数类型错误、去空白后为空、不支持的优先级、工单不存在或已关闭均报错且不写数据、不创建数据文件。
- `priority-queue` → `SupportDesk.priority_queue()`：返回待处理队列数组，每项含 `ticket`（原样完整工单，不补字段）与 `priority`（有效优先级）。包含全部未关闭工单（已首次响应、未指派、无 `opened_at` 的均在内），排除已关闭工单；按 `urgent`、`high`、`normal`、`low` 排序，同级按 `ticket_id` 区分大小写升序。未设置 `priority` 的工单按 `normal` 排序。无工单、只有已关闭工单或数据目录不存在时返回 `[]`；只读，不创建目录、不写文件，可省略输入文件。
- `response-stats` → `SupportDesk.response_stats()`：汇总首次响应耗时（`timed`、`responded`、`pending`、`untimed` 计数及 `average_minutes`、`max_minutes`），无参数，可省略输入文件，不写数据。
- `response-queue` → `SupportDesk.response_queue(as_of, target_minutes=30)`：查看模拟时刻 `as_of`（非负整数分钟）仍待首次响应的工单队列，`target_minutes` 为正整数分钟。返回 `{"untimed": n, "items": [...]}`：未关闭且无首次响应且有 `opened_at` 的工单进入 `items`，每项含完整工单 `ticket`、`waiting_minutes`（`as_of - opened_at`）与 `overdue`（等待严格大于目标才为真），按 `opened_at` 升序、同分钟按 `ticket_id` 升序；缺少 `opened_at` 的待响应工单只计入 `untimed`。任一入队工单的 `opened_at` 晚于 `as_of` 则整个查询报错。只读，不写数据。
- `response-target-report` → `SupportDesk.response_target_report(as_of, targets=None)`：按优先级判断首次响应是否达标，只反映当前数据。`as_of` 为非负整数分钟；`targets` 省略或为 `null` 时 urgent、high、normal、low 的目标分别为 5、15、30、60 分钟，传入对象时仅覆盖指定项（空对象等于默认值），目标值须为正整数。返回 `{"as_of": ..., "groups": [...]}`，groups 按 urgent、high、normal、low 排列，每组含 `priority`、`target_minutes`、整数计数 `responded`、`on_time`、`late`、`pending`、`overdue`、`untimed`、`closed_without_response` 及 `on_time_rate`。缺少 `priority` 的旧工单按 normal 统计且不补写该字段；无 `opened_at` 的工单只计 `untimed`；有登记时间且已有首次响应的工单（无论是否关闭）计 `responded`，响应耗时（`responded_at - opened_at`）不超过目标计 `on_time`，否则计 `late`；有登记时间、未响应且已关闭的工单计 `closed_without_response`；其余未响应工单计 `pending`，等待时间严格超过目标的同时计 `overdue`。`on_time_rate` 为 `on_time / responded`（0 至 1），分母为零时为 `null`。任一有登记时间工单的 `opened_at` 或已有响应时间晚于 `as_of`，整个查询报错而不返回部分结果。非法 `as_of`、非对象的非 `null` `targets`、未知优先级键或非正整数目标值均报错。空数据返回四组零计数与 `null` 达标率；只读，不创建目录或文件。
- `knowledge-publish` → `SupportDesk.publish_knowledge(article_id, ticket_id)`：从已关闭工单发布知识条目，条目含 `article_id`、`source_ticket_id`、`title`（工单 subject）、`content`（工单 resolution）四个字段并返回完整条目。两个标识去除首尾空白、区分大小写；同一 root 内 `article_id` 唯一，每个工单只可发布一次。参数非字符串或去空白后为空、标识重复、工单已发布过、工单不存在或未关闭均报错且不写数据；来源工单保持原样，无 `opened_at` 或首次响应记录的已关闭工单也可发布。
- `knowledge-search` → `SupportDesk.search_knowledge(query=None)`：检索知识条目。`query` 省略或为 `null` 时返回全部；否则须为非空白字符串，去除首尾空白后按空白拆词，以 Unicode casefold 语义忽略大小写，每个词均需作为连续子串出现在 `title` 或 `content` 中（可分别命中不同字段），标点按字面匹配。返回完整条目数组，按 `article_id` 升序，无匹配返回 `[]`。只读，不写数据；无知识条目或空目录返回 `[]`。
- `knowledge-respond` → `SupportDesk.respond_with_knowledge(ticket_id, article_id, responded_at)`：引用一条已发布的知识条目答复工单，登记首次响应，不接收自定义正文。两个标识须为非空字符串，去除首尾空白后区分大小写查找；`responded_at` 为非负整数分钟（拒绝布尔值与浮点），允许等于 `opened_at`，早于则拒绝。成功返回并保存完整目标工单，其 `first_response` 含 `message`（等于条目 `content`）、`responded_at` 与 `knowledge`（条目 `article_id`、`source_ticket_id`、`title`、`content` 四个公开字段的完整快照），重新创建 `SupportDesk` 后仍可读取同样的响应与快照。只允许存在、未关闭、有 `opened_at` 且尚无首次响应的工单使用，未指派不影响；与手写 `respond` 共用首次响应唯一性，任一种登记成功后两种入口均按已有响应拒绝。参数类型或取值非法、缺少登记时间、工单不存在或已关闭、知识条目不存在均抛出 `ValueError`，失败不写数据、不创建目录或文件、不留部分响应。成功后知识条目与来源工单保持原样，目标工单的状态、指派、备注、解决说明和优先级均不变。

命令成功向标准输出打印 JSON 并返回 0；输入或本地文件错误向标准错误输出说明并返回 2。无参数的方法可省略输入文件。数据保存在 `root/data.json`，每次成功修改后保存；适用于单进程本地使用。

## 样例

`examples/` 提供 3 份虚构业务样例。`tests/` 覆盖业务路径、拒绝非法操作后的状态和命令入口。

## 当前边界

当前没有外部消息集成或权限系统；时间由使用者提供（本地模拟时钟的非负整数分钟）。 不承诺并发写入或断电恢复。
