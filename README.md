# TodayWaifu

<p align="center">
  <a href="https://github.com/MimoKit/TodayWaifu"><img src="./ICON.png" width="160" alt="TodayWaifu ICON"></a>
</p>

<h1 align="center">TodayWaifu</h1>
<h4 align="center">GSCore / GsUID 用的鸣潮「今日老婆」插件</h4>

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

注意：图库模式会从远程图库获取并发送图片，可能存在部分图片内容风险。是否启用请自行判断；因使用图库产生的任何风险由使用者自行承担，插件作者不承担责任。

## 常用配置

| 配置 | 默认值 | 说明 |
| --- | --- | --- |
| 图片数据源 | `local` | `local` 本地资源，`gallery` 远程图库接口 |
| 本地角色图片目录 | 空 | 本地模式生效，留空自动查找 XWUID 目录 |
| 角色 ID 对照表路径 | 空 | 留空使用内置 `wife_role_id_map.txt` |
| 图库接口地址 | 默认指向官方图库 | 图库模式生效，可在控制台自行更换 |
| 今日萝莉接口地址 | `https://loli.xlinxc.cn` | GET 直接返回随机图片 |
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

## 数据

插件运行时数据写在 GSCore 的 `data/TodayWaifu/daily_wife_data.json`。

旧版本产生的其它功能数据不会再被读取；抢/送老婆只会读取当天 `wives` 里的角色记录和抢送标记。

## 其他

- 本项目仅供学习使用，请勿用于商业用途。
- 本项目采用 **GNU General Public License v3.0（GPLv3）** 开源。你可以使用、修改和分发，但需保留许可证与版权声明；分发修改版时按 GPLv3 继续开放对应源码。
