> 本项目采用 **GNU General Public License v3.0（GPLv3）** 开源。
>
> 你可以使用、修改和分发，但必须遵守 GPLv3：保留许可证与版权声明，分发修改版时按 GPLv3 继续开放对应源码。

# gs_wuwa_daily_wife

GSCore / GsUID 版鸣潮“今日老婆”插件。

插件的鸣潮角色图片只从本地角色图片目录读取，配合角色 ID 对照表抽取，不再依赖画廊接口。若开启抽群友功能，群友头像会优先使用 GSCore 缓存或本地头像缓存；没有可用头像时，会用群友 QQ 号请求 QQ 头像接口并缓存到本地后发送。

## 插件目录

```text
gs_wuwa_daily_wife
```

## 使用方法

固定触发命令：

```text
今日老婆
老婆列表
今日老公   # 需在控制台开启 DailyWifeHusbandEnabled
老公列表   # 需在控制台开启 DailyWifeHusbandEnabled
娶群友     # 需在控制台开启 DailyWifeMarryGroupMemberEnabled
```

插件禁用 GSCore 强制前缀继承，直接发送 `今日老婆` 即可触发。

触发后，插件会按当天日期、用户 ID 和当前群号固定随机一个结果；同一个用户在不同群会分别固定，不再跨群同步。

`今日老婆` 只抽女角色，`今日老公` 只抽男角色，两者每日结果相互独立、分别固定。若开启 `DailyWifeEnableGroupMember`，`今日老婆` 会按配置概率改为抽取本群群友。

发送 `老婆列表` / `今日老婆列表`（或 `老公列表` / `今日老公列表`）可以查看当前群今天已有记录，不会发送图片。

抽取流程：

1. 读取 `DailyWifeRoleMapPath` 配置的角色 ID 对照表（留空则用插件内置 `role_id_map.txt`）；
2. 在 `DailyWifeCustomRolePilePath` 配置的图片目录里，按对照表的角色 ID 查找对应子目录（留空则自动查找 `gsuid_core/data/XutheringWavesUID/custom_role_pile`）；
3. 过滤男角色（今日老婆）和所有名字包含 `漂泊者` 的角色；
4. 从有图片的角色中随机一个，再随机一张本地图片直接发送。

本地目录结构示例（子目录名为角色数字 ID）：

```text
custom_role_pile/
├── 1211/        # 达妮娅
│   ├── a.png
│   └── b.jpg
└── 1304/        # 今汐
    └── c.png
```

对照表格式为每行 `ID：角色名`，例如 `1211：达妮娅`。

## 控制台配置

- `DailyWifeCustomRolePilePath`：本地角色图片目录，留空自动查找；
- `DailyWifeRoleMapPath`：本地角色 ID 对照表路径，留空用内置表；
- `DailyWifeSendText`：是否发送“你今天的老婆是xxx”；
- `DailyWifeAtUser`：发送今日老婆和抢老婆成功图片时是否艾特对应用户；
- `DailyWifeShowRoleId`：是否显示角色 ID；
- `DailyWifeTextTemplate`：文字模板，可用变量 `{name}`、`{role_id}`；
- `DailyWifeEnableGroupMember`：是否启用「今日老婆」概率抽群友，默认关闭；
- `DailyWifeGroupMemberProbability`：「今日老婆」抽群友概率，`0` 到 `1` 之间的小数；
- `DailyWifeGroupMemberTextTemplate`：「今日老婆」命中群友时的文字模板，可用变量 `{name}`、`{user_id}`；
- `DailyWifeMarryGroupMemberEnabled`：是否启用「娶群友」命令，默认关闭；
- `DailyWifeMarryGroupMemberTextTemplate`：「娶群友」文字模板，可用变量 `{name}`、`{user_id}`；
- `DailyWifeHusbandEnabled`：是否启用「今日老公」命令，默认关闭，开启后只抽男角色；
- `DailyHusbandTextTemplate`：今日老公文字模板，可用变量 `{name}`、`{role_id}`；
- `DailyWifeMasterUnlimited`：主人无限抽老婆，开启后 GSCore 主人不会固定当天结果；
- `DailyWifeRobEnabled`：是否启用抢老婆命令；
- `DailyWifeRobSuccessRate`：抢老婆成功概率；
- `DailyWifeRobSuccessTemplate`：抢老婆成功提示，可用变量 `{name}`、`{role_id}`、`{target}`。

## 今日老公

在控制台开启 `DailyWifeHusbandEnabled` 后，可使用 `今日老公` / `老公列表` 命令，只抽取男角色，规则与今日老婆一致。

男角色需要在角色 ID 对照表里收录，且对应目录下有图片，否则会提示「没有找到可用的老公角色」。插件内置的 `role_id_map.txt` 已收录鸣潮男女角色，如需自定义可在 `DailyWifeRoleMapPath` 指定的对照表里增删。

## 抽群友 / 娶群友

群友数据来自 GSCore 的群成员缓存：

```python
CoreUser.get_group_all_user(str(ev.group_id))
```

插件不会恢复旧版 OneBot HTTP 获取群成员逻辑。头像优先使用 `CoreUser.user_icon` 中可用的本地图片路径；没有可用本地头像时，使用群友 QQ 号请求 QQ 头像接口，并下载到插件本地缓存目录后再发送：

```text
group_member_avatar_cache/{user_id}.jpg
```

相关规则：

- `DailyWifeEnableGroupMember` 开启后，`今日老婆` 才会按 `DailyWifeGroupMemberProbability` 概率抽群友；
- 概率没有命中、群成员缓存为空或头像获取失败时，`今日老婆` 会自动回退为正常抽鸣潮女角色；
- `DailyWifeMarryGroupMemberEnabled` 开启后，可直接使用 `娶群友` 命令随机抽本群群友；
- 群友候选会排除触发者自己和机器人账号；
- 群友头像缓存目录已加入 `.gitignore`，不会提交到仓库。

## 抢老婆

使用 `wl抢老婆 @对方` 或 `wl抢老婆 对方QQ` 可以抢别人今天在当前群抽到的老婆。

- 控制台关闭 `DailyWifeRobEnabled` 后，抢老婆命令不会继续执行；
- 普通用户每天只能抢一次；
- 机器人主人不受次数限制；
- 目标用户当天必须已经在当前群发送过 `今日老婆`；
- 抢老婆有成功/失败概率；
- 失败提示固定为：`抢老婆失败了，还被对方痛扁了一顿！`；
- 成功后，自己的今日老婆会被替换成对方今天记录里的老婆，并发送对方记录里的同一张图片。
