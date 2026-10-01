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
- `respond` → `SupportDesk.respond(...)`：登记首次响应，参数为 `ticket_id`、`message`、`responded_at`（非负整数分钟，不得早于 `opened_at`，相等允许），返回完整工单；不改变状态、指派、备注或解决说明。
- `response-stats` → `SupportDesk.response_stats()`：汇总首次响应耗时（`timed`、`responded`、`pending`、`untimed` 计数及 `average_minutes`、`max_minutes`），无参数，可省略输入文件，不写数据。

命令成功向标准输出打印 JSON 并返回 0；输入或本地文件错误向标准错误输出说明并返回 2。无参数的方法可省略输入文件。数据保存在 `root/data.json`，每次成功修改后保存；适用于单进程本地使用。

## 样例

`examples/` 提供 3 份虚构业务样例。`tests/` 覆盖业务路径、拒绝非法操作后的状态和命令入口。

## 当前边界

当前没有知识库、外部消息集成或权限系统；时间由使用者提供（本地模拟时钟的非负整数分钟）。 不承诺并发写入或断电恢复。
