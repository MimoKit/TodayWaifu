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

<br/>

## 丨安装提醒

> 该插件为 [早柚核心 (gsuid_core)](https://github.com/Genshin-bots/gsuid_core) 的扩展插件，必须先部署好 GsCore 框架才能使用。首次安装需重启GsCore 才能完全应用

> [!NOTE]
> 插件仍处于持续迭代中，使用中有任何问题或建议，欢迎提 [Issue](https://github.com/MimoKit/TodayWaifu/issues) 或加入交流群 **798949533** 讨论。

<br/>

## 丨我该如何安装该插件？

- 前提：你已经部署好 [gsuid_core](https://github.com/Genshin-bots/gsuid_core)。
- 将本仓库克隆到 GsCore 插件目录并重启：

```bash
cd gsuid_core/gsuid_core/plugins
git clone https://github.com/MimoKit/TodayWaifu

或向bot发送：core安装插件TodayWaifu
```

## 丨快速上手

安装完成后，在聊天窗口发送以下指令即可获取完整的可视化帮助图：

```text
今日老婆帮助
```

<br/>

## 丨数据源与图片配置

在 **GsCore 网页控制台** 配置，图片来源按功能独立选择：

| 配置项 | 作用范围 | 默认 |
| --- | --- | --- |
| `DailyWifeImageSource` | 今日老婆 / 今日老公 | `local` |
| `DailyWifeNteImageSource` | 今日异环老婆 | `gallery` |
| `DailyWifePgrImageSource` | 今日战双老婆 | `gallery` |
| `DailyLoliImageSource` | 今日萝莉 | `gallery` |

- **`local`**：只读取本地图库，不请求远程接口；本地没有图片的角色会被跳过。
- **`gallery`**：使用远程图库接口，接口不可用时按功能回退本地资源。

各功能默认值与升级前行为一致。普通老婆、今日老公和今日异环老婆默认关闭，可在控制台开启。
图库启用令牌鉴权后填写 `DailyWifeGalleryToken`，令牌请进 QQ 交流群
[798949533](https://qm.qq.com/q/pJVt8HNwrg) 获取，留空则不携带令牌。

> [!WARNING]
> 远程图库模式会从线上接口拉取并发送图片。部分图片可能存在风控风险，请自行评估是否启用；因使用远程图库产生的任何风险由部署者自行承担。

<br/>

## 丨角色台词库

抽取今日老婆时会附带一句角色台词，台词库文件位于：

```text
data/TodayWaifu/role_quotes.json
```

插件仓库内置了一份台词库，覆盖**《鸣潮》《异环》《战双帕弥什》**三类角色：首次运行会
自动写入上面这个文件，文件缺失时也会直接读取内置的那份，因此开箱即有台词，无需额外配置。

想补充**其他作品**的角色台词，直接编辑该文件，按下面的结构追加即可：

```json
{
  "role_quotes": {
    "今汐": ["苍生无言，唯剑代之。"],
    "露西亚": ["为了那些已无法战斗的人。"]
  },
  "default_quotes": ["与你相遇，是最好的礼物。"]
}
```

| 字段 | 说明 |
| --- | --- |
| `role_quotes` | 键为角色名（与抽到的角色名一致），值为台词数组；每个角色建议 5 条以上 |
| `default_quotes` | 角色没有收录台词时随机使用的兜底句子 |

- 单句台词建议控制在 **20 字以内**：超出部分会被截断，且过长会影响卡片排版。
- 该文件位于 `data` 目录，**插件更新不会覆盖你的修改**。
- 角色名需与插件抽到的名字完全一致（可在控制台开启「显示角色 ID」辅助确认）。

> [!NOTE]
> 内置台词均摘录自各游戏原作内的角色文本，**版权归原游戏厂商所有**，此处仅用于学习与交流；
> 如有侵权，请联系仓库作者删除。台词以 JSON 明文分发，部署者可自行增删。

<br/>

## Contributors

感谢所有为 TodayWaifu 提交代码、修复问题、完善文档或提出建议的人。

<a href="https://github.com/MimoKit/TodayWaifu/graphs/contributors"><img src="https://contributors-img.web.app/image?repo=MimoKit/TodayWaifu" alt="TodayWaifu contributors" width="360"></a>

当前公开署名贡献者（已排除机器人账号）：

[MimoKit](https://github.com/MimoKit) · [CWalkene](https://github.com/CWalkene) · [spaxie](https://github.com/spaxie) · [Xbaiyz12](https://github.com/Xbaiyz12)

<br/>

## Star History

<a href="https://www.star-history.com/?repos=MimoKit%2FTodayWaifu&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=MimoKit/TodayWaifu&type=date&theme=dark&legend=top-left&sealed_token=iGSy87OqFTUvED8ayYLjTFrw_W7IlBP5_jY6Q_ua8FnsJDLS0SoSUjqMvyUKaRF42CC16rhG0iVTRvAzrovXVw-AHeca_zndYF3RwQfVhE2KWan11v5JC8XjvW3z3hkkpqPEmH0CxEBpKjsWtwBTMlL_Xi16v4ig4KgoEph17U9LAGBNMDGUbsyMoz8M" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=MimoKit/TodayWaifu&type=date&legend=top-left&sealed_token=iGSy87OqFTUvED8ayYLjTFrw_W7IlBP5_jY6Q_ua8FnsJDLS0SoSUjqMvyUKaRF42CC16rhG0iVTRvAzrovXVw-AHeca_zndYF3RwQfVhE2KWan11v5JC8XjvW3z3hkkpqPEmH0CxEBpKjsWtwBTMlL_Xi16v4ig4KgoEph17U9LAGBNMDGUbsyMoz8M" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=MimoKit/TodayWaifu&type=date&legend=top-left&sealed_token=iGSy87OqFTUvED8ayYLjTFrw_W7IlBP5_jY6Q_ua8FnsJDLS0SoSUjqMvyUKaRF42CC16rhG0iVTRvAzrovXVw-AHeca_zndYF3RwQfVhE2KWan11v5JC8XjvW3z3hkkpqPEmH0CxEBpKjsWtwBTMlL_Xi16v4ig4KgoEph17U9LAGBNMDGUbsyMoz8M" />
 </picture>
</a>

<br/>

## 丨致谢与开源声明

- 感谢 [An](https://github.com/An-Sun110) 提供的老婆图库服务器支持。
- 感谢 [CWalkene](https://github.com/CWalkene) 提供的插件修改和建议。
- 本项目仅供学习与交流使用，严禁用于任何商业用途。
- 内置角色台词摘录自各游戏原作，版权归原游戏厂商所有；如有侵权，请联系删除。
- 本项目采用 **[GNU General Public License v3.0 (GPLv3)](./LICENSE)** 协议开源。
