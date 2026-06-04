# gs_wuwa_daily_wife

GSCore / GsUID 版鸣潮“今日老婆”插件。

## 插件目录

`gs_wuwa_daily_wife`

## 使用方法

固定触发命令：

```text
今日老婆
娶群友
```

插件禁用 GSCore 强制前缀继承，直接发送 `今日老婆` 或 `娶群友` 即可触发。

触发 `今日老婆` 后，插件会按当天日期、用户 ID 和当前群号固定随机一个结果；同一个用户在不同群会分别固定，不再跨群同步。

默认会随机一个鸣潮角色，然后：

1. 读取角色 ID 对照表；
2. 去 `gsuid_core/data/XutheringWavesUID/custom_role_pile/<数字ID>/`；
3. 随机取一张图片发送。

如果在控制台开启 `DailyWifeEnableGroupMember`，群聊触发时会按 `DailyWifeGroupMemberProbability` 的概率改为抽取本群成员，并发送该成员头像、名称和 QQ 号。

`娶群友` 命令会直接从本群成员里抽取群友，不走鸣潮角色池；该命令始终会发送文字反馈，即使关闭了 `DailyWifeSendText` 也不会只发图片。

群成员优先通过 `DailyWifeOneBotApiUrl` 配置的 OneBot HTTP API 调用 `get_group_member_list` 直抓；如果未配置或请求失败，会自动回退到 GSCore 已记录的本群用户缓存。

如果开启 `DailyWifeMasterUnlimited`，GSCore 主人触发 `今日老婆` 或 `娶群友` 时不会固定当天结果，可以重复随机抽取。

例如随机到“灯灯”，对照表里是 `1504：灯灯`，就会从：

```text
gsuid_core/data/XutheringWavesUID/custom_role_pile/1504/
```

里面取图发送。

## 控制台配置

- `DailyWifeCustomRolePilePath`：自定义图片目录，留空自动查找；
- `DailyWifeRoleMapPath`：自定义角色 ID 对照表，留空使用插件内置；
- `DailyWifeSendText`：是否发送“你今天的老婆是xxx”；
- `DailyWifeShowRoleId`：是否显示角色 ID；
- `DailyWifeTextTemplate`：文字模板；
- `DailyWifeEnableGroupMember`：是否启用群成员老婆，默认关闭，关闭时只抽鸣潮角色；
- `DailyWifeGroupMemberProbability`：群成员抽取概率，范围 `0~1`，例如 `0.1` 为 10%；
- `DailyWifeMasterUnlimited`：主人无限抽老婆，默认开启，开启后 GSCore 主人不会固定当天结果；
- `DailyWifeOneBotApiUrl`：OneBot HTTP API 地址，留空时只使用 GSCore 成员缓存，例如 `http://127.0.0.1:3000`；
- `DailyWifeOneBotAccessToken`：OneBot HTTP API 的 access_token，没有则留空。

> 你的 NapCat 当前只配置了反向 WebSocket，`httpServers` 为空；如果想让插件直接抓完整群成员，需要在 NapCat 开启 OneBot HTTP 服务，并把地址填到 `DailyWifeOneBotApiUrl`。不配置也能用缓存，但缓存里没有成员时会提示无法获取。

## 图片目录要求

每个角色一个数字 ID 文件夹，文件夹里放图片：

```text
custom_role_pile/
├─ 1504/
│  ├─ 1.png
│  └─ 2.jpg
├─ 1203/
│  └─ encore.webp
```

支持：`.jpg`、`.jpeg`、`.png`、`.webp`、`.gif`、`.bmp`。