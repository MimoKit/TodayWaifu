# TodayWaifu

<p align="center">
  <a href="https://github.com/nnlmc/TodayWaifu"><img src="./ICON.png" width="160" alt="TodayWaifu ICON"></a>
</p>

<h1 align="center">TodayWaifu</h1>
<h4 align="center">GSCore / GsUID 用的鸣潮「今日老婆」插件</h4>

<p align="center">
  <a href="./LICENSE"><img src="https://img.shields.io/badge/license-GPLv3-blue.svg" alt="License"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.10%2B-blue.svg" alt="Python"></a>
  <a href="https://github.com/Genshin-bots/gsuid_core"><img src="https://img.shields.io/badge/framework-GsCore-orange.svg" alt="Framework"></a>
  <a href="https://github.com/nnlmc/TodayWaifu/issues"><img src="https://img.shields.io/github/issues/nnlmc/TodayWaifu.svg" alt="Issues"></a>
  <a href="https://github.com/nnlmc/TodayWaifu/stargazers"><img src="https://img.shields.io/github/stars/nnlmc/TodayWaifu.svg?style=flat" alt="Stars"></a>
</p>

## 安装提醒

> 该插件为 [早柚核心(gsuid_core)](https://github.com/Genshin-bots/gsuid_core) 的扩展，需要先安装好 GSCore 才能使用。

>🚧 插件仍在持续完善中，欢迎提交 issue 或 PR 🚧

> 插件交流、图库账号密码获取请加群：[798949533](https://qm.qq.com/q/ejzCUfJ5l)

## 功能

- 每个用户每天在同一群/私聊里固定抽到一个鸣潮老婆，再次发送同一命令会返回当天已经抽到的老婆。
- 可以抢别人的今日老婆，也可以把自己的今日老婆送给别人（需对方确认）。
- 老公功能默认关闭，可在控制台开启后只抽男角色。
- 可选按概率从本群成员里抽到「老婆」，或直接用「娶群友」抽一个群友。
- 今日萝莉：本地图库随机发图，支持自定义接口或本地上传图片管理。
- 支持自建老婆角色（上传专属立绘），不依赖默认图库。

## 命令

| 命令 | 说明 |
| --- | --- |
| `今日老婆` | 抽取当天老婆 |
| `娶婆娘` / `jrlp` / `qlp` | `今日老婆` 的别名 |
| `老婆列表` | 查看本群今日老婆分配情况 |
| `今日老公` | 抽取当天老公（需先在控制台开启） |
| `老公列表` | 查看本群今日老公分配情况 |
| `娶群友` / `取群友` | 从本群成员里抽一个（需先在控制台开启） |
| `抢老婆 @对方` | 抢对方当天老婆 |
| `抢今日老婆 @对方` / `抢婆娘 @对方` | `抢老婆` 的别名 |
| `送老婆 @对方` | 发起送老婆请求，需要对方确认 |
| `送今日老婆 @对方` | `送老婆` 的别名 |
| `同意送老婆` | 接受别人发起的送老婆请求 |
| `拒绝送老婆` | 拒绝别人发起的送老婆请求 |
| `今日萝莉` | 随机发一张萝莉图（本地图库或自定义接口） |
| `今日萝莉上传 [图片]` / `萝莉上传图片 [图片]` | 上传图片到本地萝莉图库（需管理权限） |
| `今日萝莉列表` / `萝莉图片列表` | 查看本地萝莉图库图片 ID（需管理权限） |
| `删除萝莉图片 [图片ID]` | 删除指定或全部本地萝莉图片（需管理权限） |
| `老婆创建[角色名]` | 创建一个自定义老婆角色（需管理权限） |
| `老婆上传图片[角色名] [图片]` | 给自定义老婆上传立绘，角色不存在会自动创建（需管理权限） |
| `老婆图片列表[角色名]` / `老婆图片[角色名]` | 查看该角色已上传的图片 ID（需管理权限） |
| `老婆删除图片[角色名] [图片ID]` / `老婆删图片[角色名] [图片ID]` | 删除指定图片（需管理权限） |
| `老婆删除[角色名]` | 发起删除该角色，需在限定时间内发送「老婆删除确认」/「老婆删除取消」（需管理权限） |
| `分配老婆 @对方 角色名` / `分配今日老婆 @对方 角色名` | 仅机器人主人可用，手动指定对方的今日老婆 |
| `今日老婆帮助` | 查看插件帮助图 |

## 角色图片

角色图片由控制台「图片数据源」决定：

- `local`：读取本地 XWUID 图片目录。
- `gallery`：读取远程图库接口，需要配置图库账号密码。

本地模式默认查找：

```text
gsuid_core/data/XutheringWavesUID/custom_role_pile
gsuid_core/data/XutheringWavesUID/resource/role_pile
```

如不在默认位置，可用「本地角色图片目录」手动指定 `custom_role_pile` 路径。

注意：图库模式会从远程图库获取并发送图片，可能存在部分图片内容风险。是否启用请自行判断；因使用图库产生的任何风险由使用者自行承担，插件作者不承担责任。

## 常用配置

| 配置 | 默认值 | 说明 |
| --- | --- | --- |
| 图片数据源 | `local` | `local` 本地资源，`gallery` 远程图库接口 |
| 本地角色图片目录 | 空 | 本地模式生效，留空自动查找 XWUID 目录 |
| 角色 ID 对照表路径 | 空 | 留空使用内置 `wife_role_id_map.txt` |
| 图库接口地址 | 默认指向官方图库 | 图库模式生效，可在控制台自行更换 |
| 图库账号 / 图库密码 | 空 | 图库模式生效 |
| 今日萝莉接口地址 | 空 | 留空则今日萝莉使用本地图库 |
| 发送文字说明 | 开启 | 图片前附带“你今天的老婆是xxx” |
| 显示角色 ID | 关闭 | 开启后文字说明额外附带一行角色 ID |
| 主人 Debug 模式 | 关闭 | 开启后主人可指定抽取的角色，便于调试 |
| 启用回复前缀 | 开启 | 回复前添加「[今日老婆]」 |
| 发送时艾特触发者 | 开启 | 发送结果时艾特触发者 |
| 文字模板 | `你今天的老婆是{name}` | 支持 `{name}` 和 `{role_id}` |
| 今日老婆概率抽群友 / 概率 | 关闭 / `0.1` | 按概率从群成员里抽老婆 |
| 启用娶群友 | 关闭 | 开启后可使用「娶群友」命令 |
| 启用今日老公 | 关闭 | 开启后可使用「今日老公」命令，只抽男角色 |
| 启用抢老婆 | 开启 | 开启后可使用抢老婆命令 |
| 抢老婆成功率 | `0.5` | 0 到 1 之间的小数 |
| 抢老婆成功文案 | `抢老婆成功！你把对方今天的老婆{name}抢过来了！` | 支持 `{name}`、`{role_id}`、`{target}` |
| 启用送老婆 | 开启 | 开启后可使用送老婆命令 |
| 送老婆成功文案 | `你把今天的老婆{name}送给了对方！` | 支持 `{name}`、`{role_id}`、`{target}` |

## 数据

插件运行时数据写在 GSCore 的 `data/TodayWaifu/daily_wife_data.json`。

旧版本产生的其它功能数据不会再被读取；抢/送老婆只会读取当天 `wives` 里的角色记录和抢送标记。

## 其他

- 本项目仅供学习使用，请勿用于商业用途。
- 本项目采用 **GNU General Public License v3.0（GPLv3）** 开源。你可以使用、修改和分发，但需保留许可证与版权声明；分发修改版时按 GPLv3 继续开放对应源码。
