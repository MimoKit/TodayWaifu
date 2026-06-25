# TodayWaifu

<p align="center">
  <img src="./ICON.png" width="160" alt="TodayWaifu ICON">
</p>

<p align="center">GSCore / GsUID 用的鸣潮「今日老婆」插件。</p>

<p align="center">每日抽老婆 / 老公 / 群友，支持抢老婆、送老婆、自定义老婆图库、萝莉图库、GitHub 更新记录与图文帮助。</p>

> 插件交流、图库账号密码获取请加群：`798949533`。
>
> 本项目采用 **GNU General Public License v3.0（GPLv3）** 开源。

## 功能一览

- `今日老婆` / `娶婆娘`：抽鸣潮女角色，当天固定；可按概率抽群友。
- `今日老公`：抽鸣潮男角色，需先在配置开启。
- `今日萝莉`：从本地萝莉图库随机发一张，与老婆/老公/群友完全独立。
- `娶群友`：只抽群友，不依赖 XWUID。
- `娶群主`：只抽本群群主；群主身份靠群主自己发过消息后被插件记下来，没发言过识别不到。本群一天只能被一个人娶到，娶到的人重复发送会再看到同样的结果，其他人会被提示已经被娶走。
- `抢老婆 @对方`：抢走对方当天抽到的鸣潮角色老婆。
- `送老婆 @对方`：把自己今天的老婆送给对方。
- `老婆更新记录`：实时获取 GitHub 更新记录，通过 HTML 渲染成图片发送。
- `老婆列表` / `老公列表`：查看本群当天记录。
- `今日老婆总排行`：合并转发展示老婆被娶次数排行；配置图库账号后会同步本地记录并返回云端总排行。
- `今日老婆帮助`：发送图文帮助卡片，并注册到 GSCore「core帮助」一览页。
- 主人命令：创建 / 上传 / 查看 / 删除自定义老婆图片；下载 / 删除萝莉图库。

## 命令

### 普通命令（所有人可用）

| 命令 | 说明 | 相关配置 |
| --- | --- | --- |
| `今日老婆` / `娶婆娘` | 抽鸣潮老婆，当天固定；可配置概率抽群友 | `DailyWifeEnableGroupMember` |
| `今日老公` | 抽鸣潮老公 | `DailyWifeHusbandEnabled`；图库模式不可用 |
| `今日萝莉` | 从萝莉图库随机发一张 | 需先由主人「下载萝莉图片」 |
| `娶群友` | 只抽群友 | `DailyWifeMarryGroupMemberEnabled` |
| `娶群主` | 只抽本群群主（需群主发过消息才能识别），一群一天限一人娶到 | `DailyWifeMarryOwnerEnabled` |
| `抢老婆 @对方` / `抢今日老婆` / `抢婆娘` | 抢对方当天老婆，每天一次 | `DailyWifeRobEnabled` |
| `送老婆 @对方` / `送今日老婆` | 把自己今天的老婆送给对方 | `DailyWifeGiftEnabled` |
| `老婆更新记录` / `今日老婆更新记录` / `老婆更新日志` | 实时获取 GitHub 更新记录并渲染成图片 | `DailyWifeUpdateLogEnabled` |
| `老婆列表` / `今日老婆列表` | 查看本群今日老婆记录 | 无 |
| `老公列表` / `今日老公列表` | 查看本群今日老公记录 | 无 |
| `今日老婆总排行` / `老婆总排行` | 合并转发展示老婆被娶次数排行；配置图库账号后同步云端总排行 | `DailyWifeCloudRankEnabled` |
| `今日老婆帮助` | 发送图文帮助卡片 | 无 |

### 主人命令（自定义老婆）

| 命令 | 说明 |
| --- | --- |
| `老婆创建<角色名>` | 新建一个自定义老婆角色 |
| `老婆上传图片<角色名>`（附带图片） | 给该角色上传立绘，返回图片 ID |
| `老婆图片列表<角色名>` / `老婆图片<角色名>` | 查看已上传图片及对应图片 ID |
| `老婆删除图片<角色名> <图片ID>` | 删除该角色的某一张图片 |
| `老婆删除<角色名>` | 删除整个自定义老婆（需二次确认） |
| `老婆删除确认` / `老婆删除取消` | 确认 / 取消上一步删除 |

### 主人命令（萝莉图库）

| 命令 | 说明 |
| --- | --- |
| `下载萝莉图片` | 从远程仓库下载图包，自动 SHA256 查重，跳过重复 |
| `删除萝莉图片` | 清空全部已下载的萝莉图片 |

萝莉图库与「今日老婆 / 今日老公 / 群友 / 自定义老婆」完全无关，是独立命令和独立目录。图包来源：`https://github.com/nnlmc/waifu-gallery`。

`下载萝莉图片` 会先对直连 GitHub 测速，正常则走直连；偏慢或失败时自动切换到自建备用下载源（定时同步 GitHub 图包），并在聊天里提示切换。

### Debug 模式

开启 `DailyWifeDebugMode` 后，主人可无限抽、指定角色名，且不写入当天列表：

```text
今日老婆 达妮娅
今日老公 忌炎
```

## 安装

把插件目录放进 GSCore 的插件目录，例如：

```text
gsuid_core/gsuid_core/plugins/TodayWaifu
```

目录结构大致如下：

```text
gsuid_core/
├── gsuid_core/
│   └── plugins/
│       └── TodayWaifu/
│           ├── __init__.py
│           ├── config_default.py
│           ├── daily_wife_config.py
│           ├── role_id_map.txt     # 旧版兼容对照表
│           ├── wife_role_id_map.txt
│           ├── husband_role_id_map.txt
│           ├── ICON.png            # 插件图标（帮助一览用）
│           ├── help.png            # 「今日老婆帮助」发送的图文卡片
│           ├── help_preview.html   # 帮助图源文件（本地渲染用，已 gitignore）
│           └── README.md
└── data/
    ├── TodayWaifu/                 # 本插件全部运行时数据（见下）
    └── XutheringWavesUID/
        ├── custom_role_pile/
        └── resource/
            └── role_pile/
```

安装后重启 GSCore，或按你的部署方式刷新插件。

## 数据存储

插件所有运行时数据都写在 GSCore 的 `data/TodayWaifu/` 下，**不再写插件代码目录**，升级 / 卸载不会丢档：

```text
gsuid_core/data/TodayWaifu/
├── config.json                  # 插件配置
├── daily_wife_data.json         # 每日老婆 / 老公 / 抢老婆记录
├── group_member_avatar_cache/   # 群友头像缓存
├── custom_role_map.txt          # 自定义老婆角色 ID 对照
├── custom_role_pile/            # 自定义老婆图片
└── loli_images/                 # 萝莉图库
```

> 旧版本曾把 `config.json` 和 `daily_wife_data.json` 放在插件目录，新版本会在首次加载时**自动迁移**到 `data/TodayWaifu/`，确认无误后可手动清理插件目录下的旧文件。

## 鸣潮角色图片

角色图片有两种数据源，由控制台配置 `DailyWifeImageSource` 下拉选择决定：

- `local`：读取本地 XWUID 图片资源。
- `gallery`：读取 XWUID 图库接口并下载图片发送。

本地模式默认查找：

```text
gsuid_core/data/XutheringWavesUID/custom_role_pile
gsuid_core/data/XutheringWavesUID/resource/role_pile
```

如不在默认位置，用 `DailyWifeCustomRolePilePath` 指定目录。

图库模式使用 `DailyWifeGalleryApiUrl` / `DailyWifeGalleryUsername` / `DailyWifeGalleryPassword`。`今日老婆` 和 `今日老公` 都会按各自角色 ID 对照表过滤候选；图库接口返回的角色 ID 不在对应表内时会被跳过。

`今日老婆` 角色 ID 与角色名由插件内置 `wife_role_id_map.txt` 决定，`今日老公` 由 `husband_role_id_map.txt` 决定。也可分别用 `DailyWifeWifeRoleMapPath` / `DailyWifeHusbandRoleMapPath` 指定自己的对照表。旧配置 `DailyWifeRoleMapPath` 仍作为老婆表兼容兜底。

## 依赖说明

插件本体是独立 GSCore / GsUID 插件。鸣潮角色图片默认读取本地 XutheringWavesUID（XWUID）资源，也可在控制台切换到图库接口。

`老婆更新记录` 会实时请求 GitHub commits API，并使用 `gsuid_core.utils.html_render.render_html_to_bytes` 渲染图片；请确保运行环境的 GSCore 版本包含 HTML 渲染组件，或已安装 `pyrenderhtml>=0.0.5`。

| 功能 | 是否依赖 XWUID | 说明 |
| --- | --- | --- |
| 今日老婆 | 本地模式依赖；图库模式不依赖 | 本地读取角色图片，图库从接口获取 |
| 今日老公 | 本地模式依赖；图库模式不依赖 | 按老公角色 ID 对照表过滤候选 |
| 抢老婆 | 是 | 抢的是别人当天抽到的鸣潮角色老婆 |
| 送老婆 | 是 | 送的是自己当天抽到的鸣潮角色老婆 |
| 老婆更新记录 | 否 | 实时请求 GitHub commits API，并通过 GSCore HTML 渲染组件生成图片 |
| 老婆列表 / 老公列表 | 间接依赖 | 记录来自抽取结果 |
| 今日老婆概率抽群友 / 娶群友 | 否 | 使用 GSCore 群成员缓存和 QQ 头像 |
| 娶群主 | 否 | GSCore 本身不缓存群主身份，插件自己记录群主发言时的身份，没发言过识别不到 |
| 今日萝莉 | 否 | 使用独立萝莉图库目录 |

## 常用配置

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `DailyWifeImageSource` | `local` | 图片数据源，下拉选择；`local` 本地资源，`gallery` 远程图库接口 |
| `DailyWifeCustomRolePilePath` | 空 | 自定义本地角色图片目录，本地模式生效 |
| `DailyWifeRoleMapPath` | 空 | 自定义角色 ID 对照表，本地模式生效 |
| `DailyWifeGalleryApiUrl` | `https://img.xlinxc.cn/api/xwuid/roles` | 图库接口地址，图库模式生效 |
| `DailyWifeGalleryUsername` | 空 | 图库账号 |
| `DailyWifeGalleryPassword` | 空 | 图库密码 |
| `DailyWifeCloudRankEnabled` | 开启 | 查询总排行时是否同步本地记录并返回云端总排行；未配置图库账号密码时自动回退本地排行 |
| `DailyWifeCloudRankApiUrl` | 空 | 云端总排行接口，留空时自动从图库接口地址推导 |
| `DailyWifeSendText` | 开启 | 发图片前是否带文字 |
| `DailyWifeReplyPrefixEnabled` | 开启 | 回复是否带「[今日老婆]」前缀 |
| `DailyWifeAtUser` | 开启 | 发送结果时是否艾特触发者 |
| `DailyWifeShowRoleId` | 关闭 | 是否显示角色 ID |
| `DailyWifeDebugMode` | 关闭 | 主人调试模式 |
| `DailyWifeEnableGroupMember` | 关闭 | `今日老婆` 是否有概率抽群友 |
| `DailyWifeGroupMemberProbability` | `0.1` | 抽群友概率 |
| `DailyWifeMarryGroupMemberEnabled` | 关闭 | 是否启用 `娶群友` |
| `DailyWifeMarryOwnerEnabled` | 关闭 | 是否启用 `娶群主` |
| `DailyWifeHusbandEnabled` | 关闭 | 是否启用 `今日老公` |
| `DailyWifeRobEnabled` | 开启 | 是否启用抢老婆 |
| `DailyWifeRobSuccessRate` | `0.5` | 抢老婆成功概率 |
| `DailyWifeGiftEnabled` | 开启 | 是否启用送老婆 |
| `DailyWifeUpdateLogEnabled` | 开启 | 是否启用 `老婆更新记录` |
| `DailyWifeUpdateLogApiUrl` | `https://api.github.com/repos/nnlmc/TodayWaifu/commits?per_page=30` | GitHub commits API 地址 |
| `DailyWifeUpdateLogLimit` | `6` | 单次渲染最近多少条更新记录 |

文字模板类配置（`DailyWifeTextTemplate` / `DailyHusbandTextTemplate` / `DailyWifeGroupMemberTextTemplate` / `DailyWifeMarryGroupMemberTextTemplate` / `DailyWifeMarryOwnerTextTemplate` / `DailyWifeRobSuccessTemplate` / `DailyWifeGiftSuccessTemplate`）可在控制台自定义提示文案，支持 `{name}`、`{role_id}`、`{user_id}`、`{target}` 等变量。

## 帮助图自定义

`今日老婆帮助` 发送的是内置图片 `help.png`，插件运行时**不渲染、不依赖浏览器**。需要改样式时：

1. 编辑帮助图源文件 `help_preview.html`。
2. 本地起静态服务并用浏览器渲染，截取整页覆盖 `help.png`。
3. 只提交 `help.png`；`help_preview.html` 与渲染残留已被 `.gitignore` 忽略。

## 常见问题

### `今日老婆` 没图片

先确认 XWUID 资源目录存在：

```text
gsuid_core/data/XutheringWavesUID/custom_role_pile
gsuid_core/data/XutheringWavesUID/resource/role_pile
```

没有这些目录时，鸣潮角色相关功能无法正常发图。

### `今日萝莉` 提示还没有图片

先用主人账号发送 `下载萝莉图片`，下载完成后再发 `今日萝莉`。

### `今日老公` / `娶群友` 没反应

先在控制台开启 `DailyWifeHusbandEnabled` / `DailyWifeMarryGroupMemberEnabled`。

### `娶群主` 提示识别不到群主

开启 `DailyWifeMarryOwnerEnabled` 后，插件需要先"看到"群主自己发过一条消息才能记下其身份（GSCore 本身不缓存群主信息）；让群主在群里随便发一句话，再试一次即可。

### 抢不了别人老婆

常见原因：抢老婆功能关闭、对方今天没抽、自己已经抢过一次、抢的是自己，或对方抽到的是群友。

## 致谢

- [CWalkene](https://github.com/CWalkene)：感谢提供 PR 和改进建议。

## 开源协议

GPLv3。你可以使用、修改和分发，但需保留许可证与版权声明；分发修改版时按 GPLv3 继续开放对应源码。
