# 客户跟进工作台

按规范化邮箱登记联系人，记录跟进，并按组织检索联系人及按日期查看跟进。

## 运行

需要 Python 3.10 或更新版本，仅使用标准库，无依赖安装步骤。请在本目录运行：

```sh
python3 -m contact_flow --root ./state demo
python3 -m unittest discover -s tests -v
```

这是本地命令行程序，不监听网络端口，无账户或密码。`demo` 在指定 root 的临时子目录中读取 examples 样例并演示业务，结束后清理样例状态，不改变现有数据。

## 正常使用

公开 API：`from contact_flow import ContactFlow`，然后 `ContactFlow(root)`。每个命令接收可选的 JSON 文件，其对象键与 API 方法参数一致。例如：

```sh
python3 -m contact_flow --root ./state add examples/contacts.json
```

JSON 数组会按顺序执行多个独立操作；先前成功操作保留，后续失败不会回滚整批。重跑登记命令遇到已存在的标识会报错。

- `add` → `ContactFlow.add_contact(...)`。参数名见 `core.py` 的公开方法签名。
- `follow-up` → `ContactFlow.follow_up(...)`。参数名见 `core.py` 的公开方法签名。
- `find` → `ContactFlow.find(...)`。参数名见 `core.py` 的公开方法签名。
- `timeline` → `ContactFlow.timeline(...)`。参数名见 `core.py` 的公开方法签名。
- `merge` → `ContactFlow.merge_contacts(...)`。把源联系人合入目标联系人：删除源联系人，其全部跟进记录归入目标，返回目标联系人和转移数量。参数名见 `core.py` 的公开方法签名。
- `set-tags` → `ContactFlow.set_tags(contact_id, tags)`。整组替换联系人标签，返回规范化后的标签数组；传空数组清除标签。标签须为字符串列表，每项去除首尾空白后非空，并按 casefold 规范化、去重，返回按 Unicode 码点排序。
- `get-tags` → `ContactFlow.get_tags(contact_id)`。返回联系人的标签数组；新联系人或旧数据默认返回空数组。
- `import-contacts` → `ContactFlow.import_contacts(csv_path)`。一次性整体导入本地 CSV：参数 JSON 形如 `{"csv_path": "people.csv"}`，相对路径按当前工作目录解释。文件须为 UTF-8（可带 BOM），表头只能是 `contact_id,name,email,organization` 四列、名称精确匹配、顺序任意；缺失、重复或额外列一律拒绝。支持标准引号、字段内逗号与换行；零字段空行忽略，其余字段数不符、CSV 语法错误或编码非法一律拒绝。整份文件先全部校验：规范化后的标识或邮箱与已有联系人或批内其他记录重复（即使两行完全相同）时整批拒绝，不覆盖、不跳过、不部分写入。成功返回按文件记录顺序排列的联系人数组，内容与 `add` 返回一致；空文件或无表头一律拒绝，只有合法表头（或其后仅有空行）时返回空数组且不创建数据目录。源文件不存在报 `FileNotFoundError`、无权限报 `PermissionError`、`csv_path` 类型非法或为空白字符串报 `ValueError`。
- `find` 还接受可选 `tags`（标签数组）和 `tag_mode`（`all` 或 `any`，默认 `all`）：前者要求包含全部标签，后者要求至少匹配一个。省略 `tags` 或传 `None`、空数组时不限制标签；标签条件与组织条件同时满足才返回。
- `add-opportunity` → `ContactFlow.add_opportunity(opportunity_id, contact_id, title)`。为已存在的联系人新建销售机会，返回仅含 `opportunity_id`、`contact_id`、`title`、`stage` 的对象，初始阶段为 `new`。标识与标题去除首尾空白后须非空；标识区分大小写（`O1` 与 `o1` 不同），标题内部空白保留。一个联系人可有多个机会；机会标识在同一 root 内全局唯一，与联系人标识互不占用，重复标识（即使内容完全相同）一律拒绝。
- `set-stage` → `ContactFlow.set_stage(opportunity_id, stage)`。返回更新后的完整机会。阶段只能是 `new`（新建）、`qualified`（已确认需求）、`won`（成交）、`lost`（流失）；允许的转移只有 `new→qualified`、`new→lost`、`qualified→won`、`qualified→lost`，其余跨阶段转移一律拒绝。重复设置当前阶段视为成功但不写文件；终态（`won`/`lost`）不可再改。
- `find-opportunities` → `ContactFlow.find_opportunities(contact_id=None, stage=None)`。按可选联系人和阶段筛选，省略或传 `None` 时不限制该条件，两个条件取交集；返回按机会标识 Unicode 码点升序排列的对象数组，无匹配返回空数组。显式指定不存在的联系人报 `ValueError`（省略不校验）。旧数据没有机会时视为空集合，查询及被拒绝的操作都不会创建目录或文件。
- `funnel-report` → `ContactFlow.funnel_report(organization=None, tags=None, tag_mode="all")`。按组织生成销售漏斗统计，返回仅含 `total`、`organizations`、`csv` 的对象，只读不创建目录或改写数据。`organization`、`tags`、`tag_mode` 的筛选含义与 `find` 一致（标签规则同 `set-tags`，组织与标签条件取交集）。统计只反映机会当前阶段：`won`/`lost` 不反推为 `qualified`，没有机会的联系人只计联系人数、不计 `new`，同一联系人的多个机会逐条计数。
  - `total` 含 `contacts`、`new`、`qualified`、`won`、`lost`、`opportunities` 六个整数，依次为筛选后联系人总数、四个阶段的机会条数和机会总数。
  - `organizations` 是按组织归组的统计数组，每项比 `total` 多一个 `organization` 字段，并包含有联系人但无机会的组织。组织按 `casefold` 值归组，同组显示名取筛选后组织原值中 Unicode 码点最小者，数组按归组值升序。无匹配时 `total` 全零且数组为空；旧数据没有机会时各阶段计数为零。
  - `csv` 是字符串，表头固定为 `organization,contacts,new,qualified,won,lost,opportunities`，只输出组织行，顺序与数值与 `organizations` 一致；没有组织时只有表头。字段按标准 CSV 规则转义并保留字段内换行，记录用 LF 分隔且末尾有换行。
  - 合并联系人后，其机会按目标联系人的当前组织和标签重新归组，全局机会总数不因合并增加。`organization` 非字符串且非 `None`、标签不合法、`tag_mode` 不是 `all`/`any` 时抛 `ValueError`。
- `set-reminder` → `ContactFlow.set_reminder(contact_id, due_on, note)`。为已存在的联系人设置下一次跟进提醒。每位联系人至多保留一条提醒，再次设置整条替换；返回仅含 `contact_id`、`due_on`、`note` 的对象。`contact_id` 与 `note` 去除首尾空白后须非空，标识区分大小写，备注内部空白保留。`due_on` 只接受去除首尾空白后的 `YYYY-MM-DD` 且日期真实有效（允许过去日期与合法闰日，如 `2024-02-29`）；参数非字符串、去空白后为空或日期非法均抛 `ValueError`。未知联系人抛 `ValueError` 且不写文件。
- `clear-reminder` → `ContactFlow.clear_reminder(contact_id)`。清除联系人的提醒：有提醒则删除并返回 `true`；本就没有提醒时返回 `false` 且不写文件。标识非法或联系人未知抛 `ValueError`。
- `due-reminders` → `ContactFlow.due_reminders(as_of)`。须显式提供截止日期 `as_of`（规则同 `due_on`，不使用系统日期），返回 `due_on` 不晚于 `as_of` 的提醒数组（含当天及逾期项）。结果先按到期日升序，同日再按联系人标识的 Unicode 码点升序；无匹配返回空数组。只读，不创建目录或文件，也不改写数据；旧数据没有提醒时视为空集合。

合并联系人（`merge`）时，源联系人的全部机会（包括 `won`/`lost` 终态机会）同步归入目标联系人，机会标识、标题和阶段保持原值；返回结构与跟进计数不变。提醒也一并归入目标：只有一方有提醒时保留该条；双方都有时取到期日较早者，到期日相同则保留目标原提醒。被保留的提醒沿用所选到期日和备注，仅把 `contact_id` 改为目标标识；源提醒不再出现。记录跟进（`follow-up`）不会自动清除提醒。


命令成功向标准输出打印 JSON 并返回 0；输入或本地文件错误向标准错误输出说明并返回 2。无参数的方法可省略输入文件。数据保存在 `root/data.json`，每次成功修改后保存；适用于单进程本地使用。

## 样例

`examples/` 提供 3 份虚构业务样例。`tests/` 覆盖业务路径、拒绝非法操作后的状态和命令入口。

## 当前边界

当前不支持模糊搜索；下一次跟进提醒仅保留每位联系人的一条，不做重复或周期提醒；邮箱只做基础格式检查，不验证其真实存在。 不承诺并发写入或断电恢复。
