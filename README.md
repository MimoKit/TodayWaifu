# TodayWaifu

<p align="center">
  <a href="https://github.com/MimoKit/TodayWaifu"><img src="./ICON.png" width="160" alt="TodayWaifu ICON"></a>
</p>

<h1 align="center">TodayWaifu</h1>
<h4 align="center">GSCore / GsUID 用的鸣潮、异环「今日老婆」插件</h4>

<p align="center">
  <a href="./LICENSE"><img src="https://img.shields.io/badge/license-GPLv3-blue.svg" alt="License"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.10%2B-blue.svg" alt="Python"></a>
  <a href="https://github.com/Genshin-bots/gsuid_core"><img src="https://img.shields.io/badge/framework-GsCore-orange.svg" alt="Framework"></a>
  <a href="https://github.com/MimoKit/TodayWaifu/issues"><img src="https://img.shields.io/github/issues/MimoKit/TodayWaifu.svg" alt="Issues"></a>
  <a href="https://github.com/MimoKit/TodayWaifu/stargazers"><img src="https://img.shields.io/github/stars/MimoKit/TodayWaifu.svg?style=flat" alt="Stars"></a>
</p>

## 安装提醒

> 该插件为 [早柚核心(gsuid_core)](https://github.com/Genshin-bots/gsuid_core) 的扩展，需要先安装好 GSCore 才能使用。

>🚧 插件仍在持续完善中，欢迎提交 issue 或 PR 🚧

> 插件交流请加群：[798949533](https://qm.qq.com/q/ejzCUfJ5le)

## 使用说明

具体功能和命令请给 bot 发送：

```text
今日老婆帮助
```

bot 会返回插件帮助图，按帮助图里的说明使用即可。

## 角色图片

角色图片由控制台「图片数据源」决定：

- `local`：读取本地 XWUID 图片目录。
- `gallery`：读取远程图库接口，无需账号密码。

本地模式默认查找：

```text
gsuid_core/data/XutheringWavesUID/custom_role_pile
gsuid_core/data/XutheringWavesUID/resource/role_pile
```

如不在默认位置，可用「本地角色图片目录」手动指定 `custom_role_pile` 路径。

控制台开启「今日异环老婆」后，可发送：

```text
今日异环老婆
```

异环图片优先读取：

```text
gsuid_core/data/NTEUID/custom/panel/<角色ID>/
```

没有自定义图片时依次使用 NTEUID 自带角色图、本地缓存和官方角色立绘。

发送 `今日战双老婆`（或 `jrzslp`）可从本地战双图库抽取。默认目录为：

```text
gsuid_core/data/TodayWaifu/pgr_wife/角色名/图片文件
```

一个角色可以放多张图，也可以继续分子目录；当天重复发送会返回同一角色和图片。目录不在默认位置时，可在控制台填写「战双老婆图库目录」。

注意：图库模式会从远程图库获取并发送图片，可能存在部分图片内容风险。是否启用请自行判断；因使用图库产生的任何风险由使用者自行承担，插件作者不承担责任。

## 常用配置

| 配置 | 默认值 | 说明 |
| --- | --- | --- |
| 图片数据源 | `local` | `local` 本地资源，`gallery` 远程图库接口 |
| 本地角色图片目录 | 空 | 本地模式生效，留空自动查找 XWUID 目录 |
| 角色 ID 对照表路径 | 空 | 留空使用内置 `wife_role_id_map.txt` |
| 图库接口地址 | 默认指向官方图库 | 图库模式生效，可在控制台自行更换 |
| 今日萝莉接口地址 | `https://loli.xlinxc.cn` | GET 直接返回随机图片 |
| 官方机器人 CNB API 地址 | `https://api.cnb.cool` | QQ 官方机器人结果图直传使用的 CNB API 根地址 |
| 官方机器人 CNB 公共地址 | `https://cnb.cool` | 上传完成后用于组合公开图片 URL |
| 官方机器人 CNB 仓库 | 空 | 接收结果图片的 CNB 仓库，格式为 `组织名/仓库名` |
| 官方机器人 CNB 令牌 | 空 | 具有目标仓库图片上传权限的 Bearer Token，仅保存在部署者自己的运行配置中 |
| 图片上传白名单 | 空 | 允许使用全部图片上传功能的用户 ID；机器人主人无需加入白名单 |
| 发送文字说明 | 开启 | 图片前附带“你今天的老婆是xxx” |
| 显示角色 ID | 关闭 | 开启后文字说明额外附带一行角色 ID |
| 主人 Debug 模式 | 关闭 | 开启后主人每次抽取都会临时随机重抽，不读取或写入当天记录 |
| 指定老婆白名单 | 空 | 允许指定角色的用户 ID；主人自动放行，功能开关和权限在「今日老婆-指定老婆」服务中配置 |
| 启用回复前缀 | 开启 | 回复前添加「[今日老婆]」 |
| 发送时艾特触发者 | 开启 | 发送结果时艾特触发者 |
| 文字模板 | `你今天的老婆是{name}` | 支持 `{name}` 和 `{role_id}` |
| 今日老婆概率抽群友 / 概率 | 关闭 / `0.1` | 按概率从群成员里抽老婆 |
| 启用娶群友 | 关闭 | 开启后可使用「娶群友」命令 |
| 启用今日老公 | 关闭 | 开启后可使用「今日老公」命令，只抽男角色 |
| 启用今日异环老婆 | 关闭 | 开启后可使用「今日异环老婆」命令 |
| 启用今日战双老婆 | 开启 | 开启后可使用「今日战双老婆」或 `jrzslp` |
| 战双老婆图库目录 | 空 | 留空使用 `data/TodayWaifu/pgr_wife` |
| 异环角色 ID 对照表路径 | 空 | 留空使用内置 `nte_role_id_map.txt` |
| 异环自定义面板图目录 | 空 | 留空自动查找 `data/NTEUID/custom/panel` |
| 异环默认角色立绘目录 | 空 | 留空自动查找 `data/NTEUID/role/detail` |
| 启用抢老婆 | 开启 | 开启后可使用抢老婆命令 |
| 抢老婆成功率 | `0.5` | 0 到 1 之间的小数 |
| 抢老婆成功文案 | `抢老婆成功！你把对方今天的老婆{name}抢过来了！` | 支持 `{name}`、`{role_id}`、`{target}` |
| 启用抢老公 | 开启 | 开启后可使用抢老公命令 |
| 抢老公成功文案 | `抢老公成功！你把对方今天的老公{name}抢过来了！` | 支持 `{name}`、`{role_id}`、`{target}` |
| 启用抢萝莉 / 抢萝莉成功率 | 开启 / `0.5` | 开启后可使用抢萝莉命令 |
| 抢萝莉成功文案 | `抢萝莉成功！你把对方今天的萝莉抢过来了！` | 支持 `{name}`、`{role_id}`、`{target}` |
| 启用送老婆 | 开启 | 开启后可使用送老婆命令 |
| 送老婆成功文案 | `你把今天的老婆{name}送给了对方！` | 支持 `{name}`、`{role_id}`、`{target}` |
| 启用送老公 | 开启 | 开启后可使用送老公命令 |
| 送老公成功文案 | `你把今天的老公{name}送给了对方！` | 支持 `{name}`、`{role_id}`、`{target}` |
| 启用送萝莉 | 开启 | 开启后可使用送萝莉命令 |
| 送萝莉成功文案 | `你把今天的萝莉送给了对方！` | 支持 `{name}`、`{role_id}`、`{target}` |

图片上传命令统一使用「图片上传白名单」权限。主人始终可以上传；白名单用户可以使用「上传老婆图片」「上传萝莉图片」和「上传战双老婆图片」。战双上传只接受已经存在的角色文件夹，例如 `上传战双老婆图片 露西亚 [图片]`，不会自动创建新角色目录。

指定角色命令统一由「今日老婆-指定老婆」SV 服务管理。主人始终可以指定；其他用户需要加入「指定老婆白名单」。支持 `今日老婆 角色名`、`今日老公 角色名`、`今日异环老婆 角色名` 和 `今日战双老婆 角色名`。

### QQ 官方机器人 CNB 图片 Markdown

配置「官方机器人 CNB 仓库」和「官方机器人 CNB 令牌」后，插件会对 `qqgroup` 和 `qqguild` 平台的今日老婆结果图启用图片 Markdown。
群聊消息会将结构化艾特、回复文字和图片合并在一条 Markdown 中；私聊不会添加艾特。此实验分支仅面向 QQ 官方机器人，不再解析 CQ 码、QQ 号码参数或其他个人号协议。

插件直接调用 CNB 官方两阶段上传接口，不需要部署图库中转服务：

1. `POST {CNB API 地址}/{仓库}/-/upload/imgs` 获取 `upload_url` 和 `assets.path`。
2. `PUT upload_url` 上传图片内容。
3. 使用「CNB 公共地址」和 `assets.path` 组合 HTTPS 图片地址并发送 Markdown。

仓库和令牌默认均为空，不包含任何部署者的私人配置；未配置或上传失败时会降级为普通图片消息。

## 数据

插件运行时数据写在 GSCore 的 `data/TodayWaifu/daily_wife_data.json`。

旧版本产生的其它功能数据不会再被读取；抢/送老婆只会读取当天 `wives` 里的角色记录和抢送标记。

## 其他

- 本项目仅供学习使用，请勿用于商业用途。
- 本项目采用 **GNU General Public License v3.0（GPLv3）** 开源。你可以使用、修改和分发，但需保留许可证与版权声明；分发修改版时按 GPLv3 继续开放对应源码。
