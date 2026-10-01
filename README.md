# 客户跟进工作台

按规范化邮箱登记联系人，记录跟进，按组织检索联系人、按日期查看跟进，并通过标签分类与组合筛选联系人。

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
- `find` → `ContactFlow.find(organization=None, tags=None, tag_mode="all")`。可选 `organization` 按组织过滤；可选 `tags`（字符串数组）按标签过滤，省略、`null` 或空数组表示不限制标签。`tag_mode` 为 `all`（默认，需包含全部标签）或 `any`（含任一即可）。组织与标签条件同时满足才返回，结果按联系人标识排序。参数名见 `core.py` 的公开方法签名。
- `timeline` → `ContactFlow.timeline(...)`。参数名见 `core.py` 的公开方法签名。
- `merge` → `ContactFlow.merge_contacts(...)`。把源联系人合入目标联系人：删除源联系人，其全部跟进记录归入目标，目标标签取双方规范标签的并集，返回目标联系人和转移数量。参数名见 `core.py` 的公开方法签名。
- `set-tags` → `ContactFlow.set_tags(contact_id, tags)`。用 `tags` 字符串数组整体替换该联系人的标签并返回新标签；传入空数组清除标签。每项去除首尾空白后不得为空，再经 `casefold` 规范化并去重，内部空白保留；返回按 Unicode 码点排序。
- `get-tags` → `ContactFlow.get_tags(contact_id)`。返回该联系人的标签数组，无标签时为空数组。

命令成功向标准输出打印 JSON 并返回 0；输入或本地文件错误向标准错误输出说明并返回 2。无参数的方法可省略输入文件。数据保存在 `root/data.json`，每次成功修改后保存；适用于单进程本地使用。

## 样例

`examples/` 提供 3 份虚构业务样例。`tests/` 覆盖业务路径、拒绝非法操作后的状态和命令入口。

## 当前边界

当前不支持销售机会、提醒和模糊搜索；标签支持整体替换、读取及 `all`/`any` 组合筛选，但不做层级或别名管理；邮箱只做基础格式检查，不验证其真实存在。 不承诺并发写入或断电恢复。
