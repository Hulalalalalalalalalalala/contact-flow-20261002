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
- `update-contact` → `ContactFlow.update_contact(contact_id, changes)`。局部更新已登记联系人的资料：`changes` 是非空对象，仅允许 `name`、`email`、`organization` 中的一个或多个键，未提供的字段保留原值；成功返回与 `add` 相同结构的完整联系人对象，`contact_id` 保持不变。`contact_id` 去除首尾空白后须非空且区分大小写，标识非法或联系人不存在抛 `ValueError`。`changes` 不是对象、为空、含不允许的键，或字段值不是字符串、去首尾空白后为空，均抛 `ValueError`；`None` 不能用于清空字段。姓名与组织只去除首尾空白并保留内部空白；邮箱去除首尾空白后转小写，须恰有一个 `@`、两侧非空且不含任何空白，否则抛 `ValueError`。规范化后的邮箱与其他联系人重复时抛 `ValueError`，与自身当前邮箱相同则允许。一次调用中的所有字段共同成功或共同失败，任何拒绝都保留原数据文件字节；数据目录原本不存在时不创建目录或文件。所有字段规范化后均与当前值相同时返回当前联系人且不改写文件。组织修改立即反映在 `find` 的组织筛选和 `funnel-report` 的组织分组及 CSV 中（该联系人及其全部机会按新组织计入，未筛选的联系人总数、机会总数及各阶段总数不变）；标签、跟进记录、机会归属与阶段、提醒日期与备注均保留，旧数据缺少这些可选集合时也能更新。邮箱修改后旧邮箱可重新登记，新邮箱受既有登记与导入去重规则约束。
- `update-contacts` → `ContactFlow.update_contacts(updates)`。一次提交整批资料更新，批内所有更新共同成功或共同失败（原子）。`updates` 只接受列表，空列表返回空数组且不写入、不创建数据目录；每项是恰含 `contact_id` 与 `changes` 的对象，`changes` 规则与 `update-contact` 完全一致（非空对象，仅允许 `name`、`email`、`organization`，未提供字段保留原值，字段值与邮箱格式的校验、规范化相同）。元素不是对象、缺少或多出键、`changes` 不是非空对象或含其他字段、字段值非法，均抛 `ValueError`。标识去首尾空白后须非空且区分大小写；标识非法、联系人未知，或规范化后的标识在批内重复（即使两项完全相同）均抛 `ValueError`。邮箱唯一性按整批更新后的全部联系人判断（包含未参与更新的联系人）：批内互换或循环交换邮箱允许成功，参与者单独占用未参与者的邮箱或更新后仍存在任何重复则整批抛 `ValueError`。成功返回按输入顺序排列的完整联系人数组，结构与 `add` 一致，`contact_id` 不变，重新打开同一 root 后可查到结果。任何校验失败都不保留部分修改，原数据文件字节保持不变，原本不存在的数据目录和文件不被创建；整批结果规范化后均与原值相同时返回完整数组而不改写文件。更新同样保留标签、跟进记录、机会归属及阶段、提醒日期和备注，旧数据缺少可选集合时也能更新；组织变化立即反映在 `find` 的组织筛选和 `funnel-report` 的漏斗分组中，未筛选的联系人及机会各阶段总数不变。命令通过含 `updates` 的 JSON 对象提交一批；缺少必填参数抛 `TypeError`（经标准错误信封返回 2）。外层 JSON 数组仍是对整批方法的逐项独立调用，先前成功的批次保留。
- `follow-up` → `ContactFlow.follow_up(...)`。参数名见 `core.py` 的公开方法签名。
- `find` → `ContactFlow.find(...)`。参数名见 `core.py` 的公开方法签名。
- `timeline` → `ContactFlow.timeline(...)`。参数名见 `core.py` 的公开方法签名。
- `merge` → `ContactFlow.merge_contacts(...)`。把源联系人合入目标联系人：删除源联系人，其全部跟进记录归入目标，返回目标联系人和转移数量。参数名见 `core.py` 的公开方法签名。
- `set-tags` → `ContactFlow.set_tags(contact_id, tags)`。整组替换联系人标签，返回规范化后的标签数组；传空数组清除标签。标签须为字符串列表，每项去除首尾空白后非空，并按 casefold 规范化、去重，返回按 Unicode 码点排序。
- `get-tags` → `ContactFlow.get_tags(contact_id)`。返回联系人的标签数组；新联系人或旧数据默认返回空数组。
- `import-contacts` → `ContactFlow.import_contacts(csv_path)`。一次性整体导入本地 CSV：参数 JSON 形如 `{"csv_path": "people.csv"}`，相对路径按当前工作目录解释。文件须为 UTF-8（可带 BOM），表头只能是 `contact_id,name,email,organization` 四列、名称精确匹配、顺序任意；缺失、重复或额外列一律拒绝。支持标准引号、字段内逗号与换行；零字段空行忽略，其余字段数不符、CSV 语法错误或编码非法一律拒绝。整份文件先全部校验：规范化后的标识或邮箱与已有联系人或批内其他记录重复（即使两行完全相同）时整批拒绝，不覆盖、不跳过、不部分写入。成功返回按文件记录顺序排列的联系人数组，内容与 `add` 返回一致；空文件或无表头一律拒绝，只有合法表头（或其后仅有空行）时返回空数组且不创建数据目录。源文件不存在报 `FileNotFoundError`、无权限报 `PermissionError`、`csv_path` 类型非法或为空白字符串报 `ValueError`。
- `import-followups` → `ContactFlow.import_followups(csv_path)`。面向**已存在联系人**一次性整体批量导入跟进记录，不创建联系人。参数 JSON 形如 `{"csv_path": "followups.csv"}`，相对路径按当前工作目录解释。文件须为 UTF-8（可带 BOM），表头只能是 `contact_id,on,note` 三列、名称精确匹配、顺序任意；缺失、重复或额外列一律拒绝。支持标准引号、字段内逗号与换行；只忽略零字段空行，其余记录列数不符、CSV 语法错误或编码非法一律抛 `ValueError`。`contact_id` 去除首尾空白后须非空且区分大小写；`on` 去除首尾空白后须为真实 `YYYY-MM-DD` 日期（允许过去、未来日期与合法闰日，不读取系统日期）；`note` 去除首尾空白后须非空，保留内部空白与换行。任一字段非法或联系人未知都整批拒绝（不跳过、不部分写入），并保留原数据文件字节，数据目录原本不存在时不创建目录或文件。成功时按文件记录顺序追加，返回同顺序的跟进数组，每项仅含 `contact_id`、`on`、`note` 且值为规范化结果；原有跟进不改动，批内或与已有数据相同的记录逐条保留，重复导入同一文件也会再次追加。空文件或无表头抛 `ValueError`；合法表头后没有记录（或仅有空行）时返回空数组且不写入。重新打开同一 root 后，新记录可在 `timeline` 与 `followup-report` 中查询：沿用既有日期排序，同日同一联系人先保留原记录顺序，再排列本批记录。导入不改变联系人资料、标签、机会、提醒和漏斗统计。源文件不存在报 `FileNotFoundError`、无权限报 `PermissionError`、`csv_path` 类型非法或为空白字符串报 `ValueError`。旧数据缺少跟进集合时按空集合处理。
- `import-opportunities` → `ContactFlow.import_opportunities(csv_path)`。面向**已存在联系人**一次性整体批量导入销售机会，不创建联系人。参数 JSON 形如 `{"csv_path": "opportunities.csv"}`，相对路径按当前工作目录解释。文件须为 UTF-8（可带 BOM），表头只能是 `opportunity_id,contact_id,title,stage` 四列、名称精确匹配、顺序任意；缺失、重复或额外列一律拒绝。支持标准引号、字段内逗号与换行；只忽略零字段空行，其余记录列数不符、CSV 语法错误或编码非法一律抛 `ValueError`。四个字段去除首尾空白后均须非空：两个标识区分大小写（`O1` 与 `o1` 不同），联系人标识与机会标识互不占用；标题保留内部空白与换行；`stage` 只接受 `new`、`qualified`、`won`、`lost`，大小写不转换，导入时直接保留所填历史阶段，不做阶段转移校验、不反推历史阶段。整份文件共同成功或共同失败：每条机会只能归属已存在联系人，同一联系人可导入多个机会；规范化后的机会标识与已有机会或批内其他记录重复（即使两行内容完全相同）时整批拒绝，不覆盖、不跳过、不部分写入。未知联系人、空字段、非法阶段、记录列数不符均抛 `ValueError`。成功返回按文件记录顺序排列的完整机会数组，每项仅含 `opportunity_id`、`contact_id`、`title`、`stage`，结构与 `add-opportunity` 返回一致。空文件或无表头抛 `ValueError`；只有合法表头（或其后仅有零字段空行）时返回空数组且不写入、不创建数据目录。重新打开同一 root 后可用 `find-opportunities` 查询；`funnel-report` 按归属联系人的当前组织、标签和机会实际阶段统计。导入不改变联系人资料、标签、跟进、提醒及原有机会；旧数据缺少机会集合时按空集合处理，仍可导入。源文件不存在报 `FileNotFoundError`、无读取权限报 `PermissionError`、`csv_path` 非字符串或为空白字符串抛 `ValueError`、缺少该参数抛 `TypeError`。
- `find` 还接受可选 `tags`（标签数组）和 `tag_mode`（`all` 或 `any`，默认 `all`）：前者要求包含全部标签，后者要求至少匹配一个。省略 `tags` 或传 `None`、空数组时不限制标签；标签条件与组织条件同时满足才返回。
- `duplicate-candidates` → `ContactFlow.duplicate_candidates(organization=None, tags=None, tag_mode="all")`。在合并前查询当前 root 内的重复联系人候选，只读：不创建目录、不改写数据、不另存文件，也不执行任何合并。`organization`、`tags`、`tag_mode` 的默认值与筛选含义同 `find`（先筛选联系人，再在结果内部配对；任一成员不符合筛选条件时整对不返回）。比较时仅对姓名和组织依次做 Unicode NFKC 规范化、casefold 并删除全部 Unicode 空白，存储的原始资料不变。两人的组织比较值相同，且姓名比较值完全相同或只需一次单码点插入、删除或替换即可互转时返回该对（相邻字符交换算两次操作，不匹配；不做传递性补对）。返回数组每项仅含 `left`、`right`、`distance`：前两者是与 `add` 返回结构一致的当前完整联系人对象，`distance` 为姓名所需的最少操作数（0 或 1）。`left` 的联系人标识按 Unicode 码点小于 `right`，每对只出现一次；数组先按 `distance` 升序，再按两侧标识依次升序。无联系人、符合筛选条件的联系人少于两人或没有候选时返回空数组。`organization` 非字符串且非 `None`、标签不合法、`tag_mode` 不是 `all`/`any` 时抛 `ValueError`，空库同样校验；旧数据缺少标签集合时按空集合处理。更新、导入或合并后重新查询按当前资料与标签计算，被合并的源联系人不再出现。
- `add-opportunity` → `ContactFlow.add_opportunity(opportunity_id, contact_id, title)`。为已存在的联系人新建销售机会，返回仅含 `opportunity_id`、`contact_id`、`title`、`stage` 的对象，初始阶段为 `new`。标识与标题去除首尾空白后须非空；标识区分大小写（`O1` 与 `o1` 不同），标题内部空白保留。一个联系人可有多个机会；机会标识在同一 root 内全局唯一，与联系人标识互不占用，重复标识（即使内容完全相同）一律拒绝。
- `set-stage` → `ContactFlow.set_stage(opportunity_id, stage, on=None)`。返回更新后的完整机会。阶段只能是 `new`（新建）、`qualified`（已确认需求）、`won`（成交）、`lost`（流失）；允许的转移只有 `new→qualified`、`new→lost`、`qualified→won`、`qualified→lost`，其余跨阶段转移一律拒绝。重复设置当前阶段视为成功但不写文件、不追加历史记录；终态（`won`/`lost`）不可再改。可选的 `on` 是变更业务日期：省略或传 `None`（JSON `null`）时记录保存 `null`；提供时须为去除首尾空白后的真实 `YYYY-MM-DD` 字符串（允许过去、未来日期与合法闰日，不读取系统日期），且不得早于该机会最近一条非 `null` 记录日期（同日允许，`null` 记录不影响下限）。每次合法且真正改变阶段时追加一条仅含 `from_stage`、`to_stage`、`on` 的历史记录，阶段修改与记录追加在同一次写入中共同成功；历史不混入机会返回对象。非法日期或日期倒退抛 `ValueError`，且不改变阶段与历史。
- `stage-history` → `ContactFlow.stage_history(opportunity_id)`。按成功变更的先后顺序返回该机会的阶段历史数组（不按日期重排），每项仅含 `from_stage`、`to_stage`、`on`；新建、CSV 导入或旧数据中没有实际记录的机会返回空数组，已有阶段仅作为后续记录的起点，不反推历史。只读，不创建目录或改写数据；标识非法或机会未知抛 `ValueError`。金额设置、批量转交和联系人合并不改变历史，归属变化后仍按原机会标识查询。
- `find-opportunities` → `ContactFlow.find_opportunities(contact_id=None, stage=None)`。按可选联系人和阶段筛选，省略或传 `None` 时不限制该条件，两个条件取交集；返回按机会标识 Unicode 码点升序排列的对象数组，无匹配返回空数组。显式指定不存在的联系人报 `ValueError`（省略不校验）。旧数据没有机会时视为空集合，查询及被拒绝的操作都不会创建目录或文件。
- `transfer-opportunities` → `ContactFlow.transfer_opportunities(transfers)`。独立的批量机会归属调整，与联系人合并无关：源联系人与目标联系人都保留，不移动联系人资料、标签、跟进记录或提醒。一次提交整批转交，批内所有调整共同成功或共同失败（原子）。`transfers` 只接受列表，空列表返回空数组且不写入、不创建数据目录；缺少该参数抛 `TypeError`（经标准错误信封返回 2）。每项是恰含 `opportunity_id`、`source_contact_id`、`target_contact_id` 三个键的对象：三个标识去除首尾空白后均须为非空字符串且区分大小写，元素不是对象、缺键或多键、标识非法均抛 `ValueError`；`transfers` 不是列表同样抛 `ValueError`。三个标识中源联系人和目标联系人都须存在，机会须存在，且源联系人必须与该机会当前实际归属一致；联系人未知、机会未知或来源不符均抛 `ValueError`，空库同样校验。规范化后的机会标识在批内重复时整批拒绝——即使两项内容完全相同，或在批内连续转交同一机会，也不例外。成功返回按输入顺序排列的完整机会数组，结构与 `find-opportunities` 一致，只改变归属：`contact_id` 变为目标联系人，机会标识、标题、阶段和金额保持原值，未设置金额时不补金额字段。来源与目标相同且核对通过时该项返回原机会；整批均未改变归属时不改写文件。任何校验失败都不保留部分调整，原数据文件字节保持不变，原本不存在的数据目录和文件不被创建。四种既有阶段（含 `won`/`lost` 终态）的机会均可调整，转交不做也不改变阶段；同一批可把多个不同机会移交同一联系人。重新打开同一 root 后，`find-opportunities` 按新归属查询，`funnel-report` 和 `opportunity-amount-report` 按目标联系人的当前组织及标签重新分组和筛选，未筛选时联系人总数、机会总数、各阶段数量及金额总计不变。旧数据缺少机会集合时按空集合处理（任何机会都未知，整批拒绝），缺少其他可选集合不妨碍操作。命令通过含 `transfers` 的 JSON 对象提交一批；外层 JSON 数组仍是对整批方法的逐项独立调用，先前成功的批次保留。
- `funnel-report` → `ContactFlow.funnel_report(organization=None, tags=None, tag_mode="all")`。按组织生成销售漏斗统计，返回仅含 `total`、`organizations`、`csv` 的对象，只读不创建目录或改写数据。`organization`、`tags`、`tag_mode` 的筛选含义与 `find` 一致（标签规则同 `set-tags`，组织与标签条件取交集）。统计只反映机会当前阶段：`won`/`lost` 不反推为 `qualified`，没有机会的联系人只计联系人数、不计 `new`，同一联系人的多个机会逐条计数。
  - `total` 含 `contacts`、`new`、`qualified`、`won`、`lost`、`opportunities` 六个整数，依次为筛选后联系人总数、四个阶段的机会条数和机会总数。
  - `organizations` 是按组织归组的统计数组，每项比 `total` 多一个 `organization` 字段，并包含有联系人但无机会的组织。组织按 `casefold` 值归组，同组显示名取筛选后组织原值中 Unicode 码点最小者，数组按归组值升序。无匹配时 `total` 全零且数组为空；旧数据没有机会时各阶段计数为零。
  - `csv` 是字符串，表头固定为 `organization,contacts,new,qualified,won,lost,opportunities`，只输出组织行，顺序与数值与 `organizations` 一致；没有组织时只有表头。字段按标准 CSV 规则转义并保留字段内换行，记录用 LF 分隔且末尾有换行。
  - 合并联系人后，其机会按目标联系人的当前组织和标签重新归组，全局机会总数不因合并增加。`organization` 非字符串且非 `None`、标签不合法、`tag_mode` 不是 `all`/`any` 时抛 `ValueError`。
- `set-opportunity-amount` → `ContactFlow.set_opportunity_amount(opportunity_id, amount)`。为已存在的机会设置人民币元金额，终态（`won`/`lost`）也可设置，返回仅含 `opportunity_id` 与 `amount` 的对象。标识规则同 `set-stage`（去首尾空白后非空、区分大小写）。`amount` 只接受字符串：去首尾空白后是仅含 ASCII 数字与至多一个小数点的非负十进制数，整数部分至少一位，小数部分若出现为一至两位；允许零与前导零，不接受符号、指数、千分位分隔符及数值类型。返回与保存的金额去掉多余前导零并固定两位小数（如 `007.5` 归一为 `7.50`）。非法标识、未知机会或非法金额抛 `ValueError`，缺少必填参数抛 `TypeError`；任何失败都不改写原文件、不创建原本不存在的目录。金额与当前值相同（含未设置金额时设零）返回成功且不改写文件；金额保存至本地数据，重开同一 root 后仍有效。
- `opportunity-amount-report` → `ContactFlow.opportunity_amount_report(organization=None, tags=None, tag_mode="all")`。按组织汇总机会金额，返回仅含 `total`、`organizations`、`csv` 的对象，只读不创建目录或改写数据。参数名、默认值及筛选语义与 `funnel-report` 完全一致，非法值抛 `ValueError`，空库同样先校验。`total` 含 `new`、`qualified`、`won`、`lost`、`amount` 五个两位小数字符串：前四项按机会当前阶段逐条累计金额，`amount` 为四者之和，累加以分为单位精确计算；未设置金额的机会按 `0.00` 计入。`organizations` 每项比 `total` 多一个 `organization` 字段，分组、显示名与顺序同数量漏斗，保留有联系人但无机会的零金额组织。`csv` 表头固定为 `organization,new,qualified,won,lost,amount`，数据行与分组一致，字段按标准 CSV 规则转义并保留字段内换行，记录用 LF 分隔且末尾有换行；无匹配时 `total` 全零、分组为空、`csv` 仅含表头。阶段切换后金额归入新阶段，更新联系人资料或标签后按当前归属分组筛选，合并联系人保留金额且不重复累计；旧数据缺少机会或标签集合时按空集合处理。
- `followup-report` → `ContactFlow.followup_report(start_on, end_on, organization=None, tags=None, tag_mode="all")`。跨联系人查询指定日期范围内的跟进记录，返回仅含 `records` 和 `csv` 的对象，只读不创建目录或改写数据，也不写出导出文件。`start_on`、`end_on` 规则同 `due_on`（去除首尾空白的 `YYYY-MM-DD` 真实日期，允许合法闰日），范围包含两端，起始日晚于结束日抛 `ValueError`。`organization`、`tags`、`tag_mode` 的筛选含义与 `find` 一致（组织与标签条件取交集；省略组织或传 `None` 不限制组织，省略标签、传 `None` 或空数组不限制标签）。没有跟进记录的联系人不产生结果，机会阶段和提醒不影响查询。
  - `records` 是数组，每项只含 `contact_id`、`name`、`email`、`organization`、`on`、`note`；资料取联系人的当前值，日期和备注保留原内容。先按日期升序，再按联系人标识的 Unicode 码点升序，同日同一联系人按保存顺序排列，重复记录逐条保留。更新或合并联系人后，记录按当前联系人资料及标签筛选和展示。
  - `csv` 是字符串，表头固定为 `contact_id,name,email,organization,on,note`，行顺序和内容与 `records` 一致；字段按标准 CSV 规则转义并保留字段内换行，记录用 LF 分隔且末尾有换行。无匹配时 `records` 为空数组且 `csv` 只有表头。
  - 日期类型错误、空白、格式非法、无效日期，组织非字符串且非 `None`、标签不合法、`tag_mode` 不是 `all`/`any` 时均抛 `ValueError`，空数据时同样校验；旧数据缺少跟进或标签集合时按空集合处理。
- `stage-change-report` → `ContactFlow.stage_change_report(start_on, end_on, organization=None, tags=None, tag_mode="all")`。跨机会查询指定日期范围内的阶段变更明细，返回仅含 `records` 和 `csv` 的对象，只读不创建目录、不改写数据或另存文件。`start_on`、`end_on` 规则同 `followup-report`（去除首尾空白的 `YYYY-MM-DD` 真实日期，允许过去、未来日期与合法闰日，不读取系统日期），范围包含两端，起始日晚于结束日抛 `ValueError`。`organization`、`tags`、`tag_mode` 的默认值、规范化与筛选含义同 `followup-report`，按机会当前归属联系人的资料和标签判断。只返回 `on` 非 `null` 且在日期范围内的已保存历史记录；`on` 为 `null` 的记录排除，新建、CSV 导入或旧数据中没有实际记录的机会不补推变更。
  - `records` 是数组，每项仅含 `opportunity_id`、`contact_id`、`title`、`organization`、`from_stage`、`to_stage`、`on`：机会标识、当前归属联系人标识、机会当前标题、联系人当前组织，以及历史记录原有的 `from_stage`、`to_stage`、`on`。先按 `on` 升序，再按机会标识的 Unicode 码点升序，同日同一机会保留历史保存顺序，同日多次变更各占一行。
  - `csv` 是字符串，表头固定为 `opportunity_id,contact_id,title,organization,from_stage,to_stage,on`，行顺序和内容与 `records` 一致；字段按标准 CSV 规则转义并保留字段内换行，记录用 LF 分隔且末尾有换行。无匹配时 `records` 为空数组且 `csv` 只有表头。
  - 日期类型、格式或日历值非法，组织非字符串且非 `None`、标签不合法、`tag_mode` 不是 `all`/`any` 时均抛 `ValueError`，空库同样校验；缺少必填参数抛 `TypeError`。旧数据缺少机会、阶段历史或标签集合时按空集合处理。重开同一 root、更新资料或标签、转交机会或合并联系人后按当前归属筛选展示，历史日期与阶段不变且不重复记录。
- `win-cycle-report` → `ContactFlow.win_cycle_report(start_on, end_on, organization=None, tags=None, tag_mode="all")`。按日期范围统计成交耗时，返回仅含 `total`、`organizations`、`csv` 的对象，只读不创建目录、不改写数据或另存文件。`start_on`、`end_on` 规则同 `followup-report`（去除首尾空白的 `YYYY-MM-DD` 真实日期，允许过去、未来日期与合法闰日，不读取系统日期），范围包含两端，起始日晚于结束日抛 `ValueError`。`organization`、`tags`、`tag_mode` 的默认值、规范化与筛选含义同 `funnel-report`，按机会当前归属联系人的资料与标签判断。
  - 只计阶段历史中 `on` 非 `null`、`to_stage` 为 `won` 且成交日期在范围内的事件，每个机会至多计一次（取保存顺序中最后一条符合条件的成交记录）；没有成交历史的导入或旧机会、`on` 为 `null` 的成交均不计入，金额与提醒不影响统计。
  - 耗时取该成交记录之前按保存顺序最近一条 `to_stage` 为 `qualified` 且 `on` 非 `null` 的历史日期与成交日之间的自然日差，起点可早于查询范围；同日为零天，跨月、跨年和闰日按真实日历计算。起点不存在（或其 `on` 为 `null`）时该笔成交计为不可计算，不用其他日期补齐。
  - `total` 含 `won`、`measured`、`unmeasured`、`days`、`average`：`won` 为成交笔数，`measured` 与 `unmeasured` 分别为可计算与不可计算笔数，`days` 为可计算记录的整数总天数，`average` 为总天数除以 `measured` 后四舍五入（半数进位）保留两位小数的字符串；`measured` 为零时 `average` 为 `null`。`organizations` 每项比 `total` 多一个 `organization` 字段，组织按 `casefold` 值归组、同组显示名取入选成交归属联系人组织原值中 Unicode 码点最小者、数组按归组值升序，只保留有入选成交的组织。
  - `csv` 是字符串，表头固定为 `organization,won,measured,unmeasured,days,average`，数据行顺序与数值与 `organizations` 一致，`null` 的 `average` 输出为空字段；字段按标准 CSV 规则转义，记录用 LF 分隔且末尾有换行。无匹配时计数全零、`average` 为 `null`，组织数组为空且 `csv` 只有表头。
  - 日期类型、格式或日历值非法，组织非字符串且非 `None`、标签不合法、`tag_mode` 不是 `all`/`any` 时均抛 `ValueError`，空库同样校验；缺少必填参数抛 `TypeError`。旧数据缺少机会、阶段历史或标签集合时按空集合处理；重开 root、更新资料、转交机会或合并联系人后按当前归属重算，历史日期与阶段不变且不重复记录。
- `inactive-contacts` → `ContactFlow.inactive_contacts(as_of, inactive_days, organization=None, tags=None, tag_mode="all")`。按指定截止日查询长期未跟进联系人，只读不创建目录或改写数据，也不另存名单。`as_of` 规则同提醒截止日期（去除首尾空白的 `YYYY-MM-DD` 真实日期，允许合法闰日，不读取系统日期）；`inactive_days` 必须为正整数，布尔值不接受。`organization`、`tags`、`tag_mode` 的默认值、规范化与筛选含义同 `find`，组织与标签条件取交集。只考虑 `on` 不晚于 `as_of` 的跟进记录，取每位联系人最近的跟进日期，同日多条取保存顺序的最后一条；截止日与最近跟进日相差的自然日数（跨月、跨年、闰日按真实日历）达到 `inactive_days` 时入选，恰好达到阈值也包含。没有不晚于截止日的记录（从未跟进或仅有未来记录）也入选，其 `last_followup` 和 `idle_days` 均为 `null`。机会阶段、机会数量与提醒不影响入选，同一联系人只出现一次。
  - 返回数组，每项仅含 `contact`、`last_followup`、`idle_days`：`contact` 是与 `add` 返回结构一致的当前联系人，`last_followup` 是最近一条完整跟进记录（仅含 `contact_id`、`on`、`note`）或 `null`，`idle_days` 是间隔自然日数或 `null`。先列无截止日内记录者并按联系人标识 Unicode 码点升序，其余按间隔天数降序、标识升序；无匹配返回空数组。
  - 日期或天数非法、组织非字符串且非 `None`、标签非法、`tag_mode` 不是 `all`/`any` 时抛 `ValueError`，空库同样校验参数；缺少必填参数抛 `TypeError`。旧数据缺少跟进或标签集合时按空集合处理；重新打开 root、更新资料、导入跟进或合并联系人后按当前资料、标签和记录归属重新计算。
- `set-reminder` → `ContactFlow.set_reminder(contact_id, due_on, note)`。为已存在的联系人设置下一次跟进提醒。每位联系人至多保留一条提醒，再次设置整条替换；返回仅含 `contact_id`、`due_on`、`note` 的对象。`contact_id` 与 `note` 去除首尾空白后须非空，标识区分大小写，备注内部空白保留。`due_on` 只接受去除首尾空白后的 `YYYY-MM-DD` 且日期真实有效（允许过去日期与合法闰日，如 `2024-02-29`）；参数非字符串、去空白后为空或日期非法均抛 `ValueError`。未知联系人抛 `ValueError` 且不写文件。
- `clear-reminder` → `ContactFlow.clear_reminder(contact_id)`。清除联系人的提醒：有提醒则删除并返回 `true`；本就没有提醒时返回 `false` 且不写文件。标识非法或联系人未知抛 `ValueError`。
- `complete-reminder` → `ContactFlow.complete_reminder(contact_id, on, note, next_reminder=None)`。完成联系人当前提醒：一次操作追加一条跟进记录并处理下一次提醒，两者共同成功或共同失败。返回仅含 `followup` 与 `reminder` 的对象，前者结构与 `follow-up` 的记录一致，后者与 `set-reminder` 的提醒结构一致，没有后续提醒时为 `null`。`contact_id`、`on`、`note` 的校验分别同 `set-reminder` 的标识、`due_on` 的日期规则与备注规则（`on` 允许早于或晚于原到期日，不读取系统日期）。省略 `next_reminder` 或传 `null` 时清除原提醒；提供时它必须是恰含 `due_on` 与 `note` 的对象，字段校验同 `set-reminder`，且新到期日须严格晚于 `on`。参数非法、未知联系人、没有当前提醒、`next_reminder` 缺项或含额外键、新到期日不晚于完成日期均抛 `ValueError`，且不追加跟进、不清除或替换提醒、不改写数据文件；旧数据缺少提醒集合时视为没有提醒。
- `due-reminders` → `ContactFlow.due_reminders(as_of)`。须显式提供截止日期 `as_of`（规则同 `due_on`，不使用系统日期），返回 `due_on` 不晚于 `as_of` 的提醒数组（含当天及逾期项）。结果先按到期日升序，同日再按联系人标识的 Unicode 码点升序；无匹配返回空数组。只读，不创建目录或文件，也不改写数据；旧数据没有提醒时视为空集合。

合并联系人（`merge`）时，源联系人的全部机会（包括 `won`/`lost` 终态机会）同步归入目标联系人，机会标识、标题和阶段保持原值；返回结构与跟进计数不变。提醒也一并归入目标：只有一方有提醒时保留该条；双方都有时取到期日较早者，到期日相同则保留目标原提醒。被保留的提醒沿用所选到期日和备注，仅把 `contact_id` 改为目标标识；源提醒不再出现。记录跟进（`follow-up`）不会自动清除提醒。


命令成功向标准输出打印 JSON 并返回 0；输入或本地文件错误向标准错误输出说明并返回 2。无参数的方法可省略输入文件。数据保存在 `root/data.json`，每次成功修改后保存；适用于单进程本地使用。

## 样例

`examples/` 提供 3 份虚构业务样例。`tests/` 覆盖业务路径、拒绝非法操作后的状态和命令入口。

## 当前边界

当前不支持模糊搜索；下一次跟进提醒仅保留每位联系人的一条，不做重复或周期提醒；邮箱只做基础格式检查，不验证其真实存在。 不承诺并发写入或断电恢复。
