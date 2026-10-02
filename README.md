# TodayWaifu

<p align="center">
  <a href="https://github.com/MimoKit/TodayWaifu"><img src="./ICON.png" width="160" alt="TodayWaifu ICON"></a>
</p>

<h1 align="center">TodayWaifu</h1>
<h4 align="center">✨ 基于 GsCore 框架的多游戏「今日老婆」娱乐插件 ✨</h4>

<div align="center">
  <a href="https://github.com/Genshin-bots/gsuid_core">早柚核心</a> &nbsp;·&nbsp;
  <a href="https://blog.xlinxc.cn/posts/botplugin/todaywaifu-plugin-intro">详细文档</a> &nbsp;·&nbsp;
  <a href="https://qm.qq.com/q/pJVt8HNwrg">交流 Q 群 (798949533)</a> &nbsp;·&nbsp;
  <a href="https://github.com/MimoKit/TodayWaifu/issues">问题反馈</a>
</div>

<div align="center">
  <a href="https://count.getloli.com/"><img src="https://count.getloli.com/get/@TodayWaifu?theme=moebooru" alt="TodayWaifu 访问计数"></a>
</div>

<br/>

> 详细配置与使用指南请查阅：[TodayWaifu 完整使用指南与配置手册](https://blog.xlinxc.cn/posts/botplugin/todaywaifu-plugin-intro)

<br/>

## 丨安装提醒

> 本插件是 [早柚核心 (gsuid_core)](https://github.com/Genshin-bots/gsuid_core) 的扩展插件，须先完成 GsCore 框架部署方可使用。首次安装后需重启 GsCore 才能完全生效

> [!NOTE]
> 插件仍在持续迭代。使用中如遇问题或有改进建议，欢迎提交 [Issue](https://github.com/MimoKit/TodayWaifu/issues)，或加入交流群 **798949533** 讨论。

<br/>

## 丨安装步骤

- 前提：已部署 [gsuid_core](https://github.com/Genshin-bots/gsuid_core)。
- 将本仓库克隆至 GsCore 插件目录，随后重启：

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

### 图库访问令牌

图库的**列表接口与图片本身均要求鉴权**，必须在控制台填写
`DailyWifeGalleryToken`，否则拉取列表和下载图片都会返回 403。

令牌请进 QQ 交流群 [798949533](https://qm.qq.com/q/pJVt8HNwrg) 获取，
或前往 <https://twf.xlinxc.cn> 自助申请；留空则不携带令牌。注意：无论选择哪种方式，最终都须加入该交流群，未加群者不予审核。

插件会在列表与图片两类请求上都自动携带该令牌；当图库因限流、封禁或配额拒绝时，
会提示对应的处理方式，而不会笼统地提示检查令牌。列表返回的图片地址自带短期签名，
插件下载图片时**同时**携带令牌，因此即使签名已过期（例如读取昨天抽到的老婆记录）
也能正常取图。

> [!WARNING]
> 远程图库模式会从线上接口拉取并发送图片。部分图片可能存在风控风险，请自行评估是否启用；因使用远程图库产生的任何风险由部署者自行承担。

<br/>

## Contributors

感谢所有为 TodayWaifu 提交代码、修复问题、完善文档或提出建议的人。

<a href="https://github.com/MimoKit/TodayWaifu/graphs/contributors"><img src="https://contributors-img.web.app/image?repo=MimoKit/TodayWaifu&max=100" alt="TodayWaifu contributors" height="48"></a>

当前公开署名贡献者（已排除机器人账号）：

[MimoKit](https://github.com/MimoKit) · [CWalkene](https://github.com/CWalkene) · [spaxie](https://github.com/spaxie) · [Xbaiyz12](https://github.com/Xbaiyz12) · [xiaolinlino](https://github.com/xiaolinlino) · [zory1117](https://github.com/zory1117)

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
- 本项目采用 **[GNU General Public License v3.0 (GPLv3)](./LICENSE)** 协议开源。
