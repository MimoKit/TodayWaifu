# TodayWaifu

<p align="center">
  <a href="https://github.com/MimoKit/TodayWaifu"><img src="./ICON.png" width="160" alt="TodayWaifu ICON"></a>
</p>

<h1 align="center">TodayWaifu</h1>
<h4 align="center">✨ 基于 GsCore 框架的多游戏「今日老婆」娱乐插件 ✨</h4>

<div align="center">
  <a href="https://github.com/Genshin-bots/gsuid_core">早柚核心</a> &nbsp;·&nbsp;
  <a href="https://qm.qq.com/q/pJVt8HNwrg">交流 Q 群 (798949533)</a> &nbsp;·&nbsp;
  <a href="https://github.com/MimoKit/TodayWaifu/issues">问题反馈</a>
</div>

<div align="center">
  <a href="https://count.getloli.com/"><img src="https://count.getloli.com/get/@TodayWaifu?theme=moebooru" alt="TodayWaifu 访问计数"></a>
</div>

<p align="center">
  <img src="./preview.gif" alt="TodayWaifu Preview" width="300">
</p>

## Star History

<a href="https://www.star-history.com/?repos=MimoKit%2FTodayWaifu&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=MimoKit/TodayWaifu&type=date&theme=dark&legend=top-left&sealed_token=iGSy87OqFTUvED8ayYLjTFrw_W7IlBP5_jY6Q_ua8FnsJDLS0SoSUjqMvyUKaRF42CC16rhG0iVTRvAzrovXVw-AHeca_zndYF3RwQfVhE2KWan11v5JC8XjvW3z3hkkpqPEmH0CxEBpKjsWtwBTMlL_Xi16v4ig4KgoEph17U9LAGBNMDGUbsyMoz8M" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=MimoKit/TodayWaifu&type=date&legend=top-left&sealed_token=iGSy87OqFTUvED8ayYLjTFrw_W7IlBP5_jY6Q_ua8FnsJDLS0SoSUjqMvyUKaRF42CC16rhG0iVTRvAzrovXVw-AHeca_zndYF3RwQfVhE2KWan11v5JC8XjvW3z3hkkpqPEmH0CxEBpKjsWtwBTMlL_Xi16v4ig4KgoEph17U9LAGBNMDGUbsyMoz8M" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=MimoKit/TodayWaifu&type=date&legend=top-left&sealed_token=iGSy87OqFTUvED8ayYLjTFrw_W7IlBP5_jY6Q_ua8FnsJDLS0SoSUjqMvyUKaRF42CC16rhG0iVTRvAzrovXVw-AHeca_zndYF3RwQfVhE2KWan11v5JC8XjvW3z3hkkpqPEmH0CxEBpKjsWtwBTMlL_Xi16v4ig4KgoEph17U9LAGBNMDGUbsyMoz8M" />
 </picture>
</a>

<br/>

## 丨安装提醒

> 该插件为 [早柚核心 (gsuid_core)](https://github.com/Genshin-bots/gsuid_core) 的扩展插件，必须先部署好 GsCore 框架才能使用。首次安装需重启GsCore 才能完全应用

> [!NOTE]
> 插件仍处于持续迭代中，使用中有任何问题或建议，欢迎提 [Issue](https://github.com/MimoKit/TodayWaifu/issues) 或加入交流群 **798949533** 讨论。

<br/>

## 丨快速上手

安装完成后，在聊天窗口发送以下指令即可获取完整的可视化帮助图：

```text
今日老婆帮助
```

### 常用功能

| 触发指令 | 功能说明 | 备注 |
| :--- | :--- | :--- |
| `来点老婆` | 从独立远程图库临时随机取一张图 | 每人每天 3 次，不写每日记录 |
| `今日老婆` | 随机抽取今天的专属老婆 | 默认鸣潮角色 |
| `今日老公` | 随机抽取今天的专属老公 | 需在控制台开启 |
| `今日战双老婆` / `jrzslp` | 抽取战双帕弥什角色老婆 | 读本地图库 |
| `今日异环老婆` | 抽取异环角色老婆 | 需在控制台开启 |
| `今日萝莉` | 抽取当天固定的萝莉图片 | 支持 JSON API/本地图库 |
| `娶群友` | 从当前群成员里抽一个当老婆 | 需在控制台开启 |
| `抢老婆` / `抢老公` / `抢萝莉` | 尝试抢走别人今天的老婆/老公/萝莉 | 默认 50% 成功率 |
| `送老婆` / `送老公` / `送萝莉` | 把自己今天的老婆/老公/萝莉送给别人 | 需对方同意 |
| `离婚` / `老婆离婚` | 放弃当天抽到的老婆 | 只处理今日老婆，不影响今日老公、萝莉等其他记录 |
| `老公离婚` / `萝莉离婚` | 放弃当天抽到的老公或萝莉 | 按指令逐个处理对应记录 |
| `创建老婆` / `删除老婆` | 自定义创建/删除专属角色 | 支持上传本地图片 |

<br/>

## 丨数据源与图片配置

插件支持多种图片来源模式，可在 **GsCore 网页控制台** 灵活配置：

- **`local`（本地模式，默认）**：优先读取本地 `XutheringWavesUID`、`NTEUID` 等插件的角色图片。
- **`gallery`（图库模式）**：自动调用远程 API 获取图片，无需手动配置本地图片资源。

图库接口启用令牌鉴权后，需在控制台填写 **图库访问令牌**（`DailyWifeGalleryToken`）才能正常取图。
令牌请进 QQ 交流群 [798949533](https://qm.qq.com/q/pJVt8HNwrg) 获取；留空则请求不携带令牌，
适用于未启用鉴权的部署。

### 来点老婆

`来点老婆` 走独立的 **来点老婆图库接口**（`DailyWifeRandomGalleryApiUrl`），每次临时随机取一张图，
不写入每日记录，也不参与抢、送或离婚；接口和图片下载复用同一个「图库访问令牌」。

该命令默认限制每人每天 3 次，按「同一个群」单独计算：同一个人在不同群各有独立次数，私聊单独算一份，
每天零点自动重置。次数可在控制台 **来点老婆每日次数**（`DailyWifeRandomDailyLimit`）调整，填 `0`
表示不限制；机器人主人不受限制。取图失败（接口异常、令牌无效等）不会消耗次数。

> [!WARNING]
> 远程图库模式会从线上接口拉取并发送图片。部分图片可能存在风控风险，请自行评估是否启用；因使用远程图库产生的任何风险由部署者自行承担。

<br/>

## 丨功能特色与高级配置

不用手动修改任何配置文件！所有选项均已挂载至 **GsCore 网页控制台**：

- **自定义角色与上传**：白名单用户及机器人主人支持使用 `上传老婆图片`、`上传萝莉图片` 等指令自定义图库。
- **指定角色抽卡**：支持白名单用户直接指定抽取特定角色（如 `今日老婆 散华`）。
- **QQ 官方机器人直传**：支持配置 CNB 仓库与 Token，自动为 QQ 官方机器人启用 Markdown + 交互按钮（摸头/离婚）。
- **文案与概率可调节**：所有抽卡文案、抢夺成功率、抽群友概率均可在控制台实时修改，即刻生效。

<br/>

## 丨数据存储

- 每日抽取与交互数据保存在 GsCore 根目录下的 `data/data.db` 中。
- 插件配置自动保存在 `data/TodayWaifu/config.json`，无需手动编辑。

<br/>

## 丨致谢与开源声明

- 感谢 [An](https://github.com/An-Sun110) 提供的老婆图库服务器支持。
- 感谢 [CWalkene](https://github.com/CWalkene) 提供的插件修改和建议。
- 本项目仅供学习与交流使用，严禁用于任何商业用途。
- 本项目采用 **[GNU General Public License v3.0 (GPLv3)](./LICENSE)** 协议开源。
