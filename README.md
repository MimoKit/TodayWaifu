<div align="center">

<img src="./ICON.png" width="160" alt="TodayWaifu ICON">

# TodayWaifu

_基于 [GsCore（gsuid_core）](https://github.com/Genshin-bots/gsuid_core) 的多游戏「今日老婆」娱乐插件_

[![License: GPLv3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![GsCore](https://img.shields.io/badge/GsCore-%E6%97%A9%E6%A6%86%E6%A0%B8%E5%BF%83-8f5db7)](https://github.com/Genshin-bots/gsuid_core)
[![Python](https://img.shields.io/badge/Python-%E2%89%A53.11-3776ab)](pyproject.toml)

每天零点一过，全群一起抽今日老婆 —— 还能抢、能送、能娶群友。

<a href="https://count.getloli.com/"><img src="https://count.getloli.com/get/@TodayWaifu?theme=moebooru" alt="TodayWaifu 访问计数"></a>

[早柚核心](https://github.com/Genshin-bots/gsuid_core) &nbsp;·&nbsp; [交流 Q 群 (798949533)](https://qm.qq.com/q/pJVt8HNwrg) &nbsp;·&nbsp; [问题反馈](https://github.com/MimoKit/TodayWaifu/issues)

</div>

## ✨ 特性

- **多游戏同抽** —— 鸣潮、异环、战双、萝莉、正太，各玩各的互不干扰
- **全天固定** —— 每天抽一次，当日结果全员可见、可抢可送
- **图源灵活** —— 每个功能可独立选择本地图片或远程图库
- **出图不卡** —— 图片走独立投递队列，命令秒回不发呆

> [!TIP]
> 插件指令与玩法较多，安装后发送 **`今日老婆帮助`** 获取完整的可视化帮助图，一图看懂全部玩法。

<br/>

## 📦 安装

> 该插件是 [早柚核心 (gsuid_core)](https://github.com/Genshin-bots/gsuid_core) 的扩展插件，必须先部署好 GsCore 框架才能使用。

```bash
cd gsuid_core/gsuid_core/plugins
git clone https://github.com/MimoKit/TodayWaifu
```

或直接向 bot 发送：`core安装插件TodayWaifu`

安装后**重启 GsCore**，发送 `今日老婆帮助` 开始使用。

<br/>

## ⚙️ 配置

在 **GsCore 网页控制台** 中配置。图片来源按**功能独立**开关：

| 配置项 | 作用范围 | 默认 |
| --- | --- | --- |
| `DailyWifeImageSource` | 今日老婆 / 今日老公 | `local` |
| `DailyWifeNteImageSource` | 今日异环老婆 | `gallery` |
| `DailyWifePgrImageSource` | 今日战双老婆 | `gallery` |
| `DailyLoliImageSource` | 今日萝莉 | `gallery` |

- **`local`（本地模式）**：只读取本地 `XutheringWavesUID`、`NTEUID` 等插件的角色图片目录，完全不请求远程接口；本地没有图片的角色会被跳过。
- **`gallery`（图库模式）**：使用远程图库接口取图；接口不可用或本地已有图片时回退本地目录。

各功能默认值与其升级前的实际行为一致，升级后表现不变。

图库接口启用令牌鉴权后，需在控制台填写 **图库访问令牌**（`DailyWifeGalleryToken`）才能正常取图。
令牌请进 QQ 交流群 [798949533](https://qm.qq.com/q/pJVt8HNwrg) 获取；留空则请求不携带令牌，
适用于未启用鉴权的部署。

> [!WARNING]
> 远程图库模式会从线上接口拉取并发送图片。部分图片可能存在风控风险，请自行评估是否启用；因使用远程图库产生的任何风险由部署者自行承担。

<br/>

## 🏗 架构

```mermaid
flowchart LR
    A["今日老婆 / 抢 / 送 …"] --> B["触发器 + 每日分配<br/>当日结果固定"]
    B --> C["候选加载<br/>本地图库 / 远程图库"]
    C --> D["图片管线<br/>下载 · WebP 动态压缩"]
    D --> E["投递队列<br/>常驻 worker 消费"]
    E --> F[群图片消息]
```

- 每日分配基于日期种子，同一群、同一天结果一致
- 命令协程只负责入队（毫秒级返回），图片下载与发送由常驻 worker 消费，插件重载后自动补齐 worker
- 远程图库带磁盘缓存与断路器，接口抖动时自动回退本地图库
- 数据全部落在 GsCore 的 data 目录，卸载即清

<br/>

## 📄 开源许可

- 本项目采用 **[GNU General Public License v3.0 (GPLv3)](./LICENSE)** 协议开源，仅供学习与交流使用，严禁用于任何商业用途。
- 感谢 [An](https://github.com/An-Sun110) 提供的老婆图库服务器支持。
- 感谢 [CWalkene](https://github.com/CWalkene) 提供的插件修改和建议。
- 使用中有任何问题或建议，欢迎提 [Issue](https://github.com/MimoKit/TodayWaifu/issues) 或加入交流群 **798949533** 讨论。

<br/>

## Star History

<a href="https://www.star-history.com/?repos=MimoKit%2FTodayWaifu&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=MimoKit/TodayWaifu&type=date&theme=dark&legend=top-left&sealed_token=iGSy87OqFTUvED8ayYLjTFrw_W7IlBP5_jY6Q_ua8FnsJDLS0SoSUjqMvyUKaRF42CC16rhG0iVTRvAzrovXVw-AHeca_zndYF3RwQfVhE2KWan11v5JC8XjvW3z3hkkpqPEmH0CxEBpKjsWtwBTMlL_Xi16v4ig4KgoEph17U9LAGBNMDGUbsyMoz8M" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=MimoKit/TodayWaifu&type=date&legend=top-left&sealed_token=iGSy87OqFTUvED8ayYLjTFrw_W7IlBP5_jY6Q_ua8FnsJDLS0SoSUjqMvyUKaRF42CC16rhG0iVTRvAzrovXVw-AHeca_zndYF3RwQfVhE2KWan11v5JC8XjvW3z3hkkpqPEmH0CxEBpKjsWtwBTMlL_Xi16v4ig4KgoEph17U9LAGBNMDGUbsyMoz8M" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=MimoKit/TodayWaifu&type=date&legend=top-left&sealed_token=iGSy87OqFTUvED8ayYLjTFrw_W7IlBP5_jY6Q_ua8FnsJDLS0SoSUjqMvyUKaRF42CC16rhG0iVTRvAzrovXVw-AHeca_zndYF3RwQfVhE2KWan11v5JC8XjvW3z3hkkpqPEmH0CxEBpKjsWtwBTMlL_Xi16v4ig4KgoEph17U9LAGBNMDGUbsyMoz8M" />
 </picture>
</a>
