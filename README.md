# TodayWaifu

<p align="center">
  <a href="https://github.com/MimoKit/TodayWaifu"><img src="./ICON.png" width="160" alt="TodayWaifu ICON"></a>
</p>

<h1 align="center">TodayWaifu</h1>
<h4 align="center">基于 GsCore 框架的多游戏「今日老婆」娱乐插件</h4>

<div align="center">
  <a href="https://github.com/Genshin-bots/gsuid_core">早柚核心</a> &nbsp;·&nbsp;
  <a href="https://blog.xlinxc.cn/posts/botplugin/todaywaifu-plugin-intro">官方文档站</a> &nbsp;·&nbsp;
  <a href="https://qm.qq.com/q/pJVt8HNwrg">交流 Q 群 (798949533)</a> &nbsp;·&nbsp;
  <a href="https://github.com/MimoKit/TodayWaifu/issues">问题反馈</a>
</div>

<div align="center">
  <a href="https://count.getloli.com/"><img src="https://count.getloli.com/get/@TodayWaifu?theme=moebooru" alt="TodayWaifu 访问计数"></a>
</div>

<br/>

> 完整功能指令手册与全部中文配置项详解请访问：  
> **[TodayWaifu 完整使用指南与配置手册](https://blog.xlinxc.cn/posts/botplugin/todaywaifu-plugin-intro)**

---

## 插件说明

TodayWaifu 是基于 GsCore（早柚核心）机器人框架开发的多游戏角色抽取与互动插件，支持鸣潮、战双帕弥什、异环等作品的角色抽取，并提供抢老婆、送老婆、离婚、娶群友等丰富的社群互动玩法。

支持本地角色目录与云端鉴权图库双数据源，自带低速预热与容量淘汰缓存机制。

---

## 安装步骤

### 前提条件
已完成部署并启动 [GsCore（早柚核心）](https://github.com/Genshin-bots/gsuid_core) 框架。

### 方式一：机器人指令安装
向机器人发送以下指令即可自动拉取安装：

```text
core安装插件TodayWaifu
```

### 方式二：手动克隆安装
进入 GsCore 的插件目录进行克隆：

```bash
cd gsuid_core/gsuid_core/plugins
git clone https://github.com/MimoKit/TodayWaifu
```

安装完成后，请重启 GsCore 进程以使插件完全生效。

---

## 快速上手

在群聊或私聊中发送以下指令即可查看完整的可视化帮助图：

```text
今日老婆帮助
```

常用基础指令一览：

- `今日老婆`：抽取今天的老婆角色（默认鸣潮女角色）
- `今日老公`：抽取今天的老公角色（控制台开启后生效）
- `今日战双老婆`：抽取战双帕弥什角色
- `今日异环老婆`：抽取异环角色
- `今日普通老婆`：抽取经典动漫跨作品角色
- `老婆列表` / `老公列表`：查看本群今日抽取汇总
- `抢老婆 @某人` / `送老婆 @某人`：群友间老婆转让与互动
- `离婚`：主动放弃今天的老婆
- `娶群友`：按概率或独立指令迎娶当前群友

---

## 控制台配置项概览

在 **GsCore 网页控制台** 中的「插件配置」->「TodayWaifu」进行可视化修改。以下为核心常用配置：

| 配置项中文名称 | 适用功能 | 可选值 / 默认值 | 说明 |
| :--- | :--- | :--- | :--- |
| **图片数据源** | 今日老婆 / 今日老公 | `local` / `gallery`（默认 `local`） | 选择 `local` 读取服务器本地图片目录；选择 `gallery` 使用云端远程图库。 |
| **异环老婆图片数据源** | 今日异环老婆 | `local` / `gallery`（默认 `gallery`） | 本地缺图时自动从 NTEUID 官方资源地址兜底。 |
| **战双老婆图片数据源** | 今日战双老婆 | `local` / `gallery`（默认 `gallery`） | 优先请求远程图库，不可用时自动回落本地战双目录。 |
| **萝莉图片数据源** | 今日萝莉 | `local` / `gallery`（默认 `gallery`） | 控制萝莉图片的获取渠道。 |
| **图库访问令牌** | 远程图库鉴权 | 字符串（默认留空） | 启用远程图库鉴权后必填，前往申请平台或交流群获取。 |
| **图库接口统一地址** | 远程图库数据源 | 默认 `https://twfapi.xlinxc.cn` | 远程图库统一数据接口。 |
| **零点前预热图库图片** | 性能与带宽控制 | `True` / `False`（默认开启） | 每天 23:20 提前低速缓存次日候选图片，避免零点并发卡顿。 |
| **角色图片最大体积(MB)** | 风控与上传优化 | 默认 `2` MB | 发送前自动压缩超过设定大小的图片，避免官方机器人平台上传超时。 |
| **发送角色剧情与对话台词** | 台词展示 | 默认关闭 | 开启后抽取结果附加角色专属剧情语音与对话台词。 |

> 完整 40+ 项配置参数说明（包含群友概率、抢夺成功率、自定义文案模板、各游戏对照表路径等）请参阅官方文档站：  
> **[TodayWaifu 完整使用指南与配置手册](https://blog.xlinxc.cn/posts/botplugin/todaywaifu-plugin-intro)**

---

## 图库访问令牌

当使用 `gallery` 远程图库模式时，列表与图片均需鉴权访问，必须在控制台填写「**图库访问令牌**」。

- 自助申请平台：<https://twf.xlinxc.cn>
- 官方交流 Q 群：**798949533**

请加入官方交流群完成审核；未填写有效令牌时请求远程图库将提示鉴权失败。

---

## 社区公共图库（WaifuHub）

为了让更多群友参与收集与补充角色插画，项目配套开源了公共图片投递仓库：

- **仓库地址**：[https://github.com/MimoKit/WaifuHub](https://github.com/MimoKit/WaifuHub)
- **投递规则**：按游戏名称、男女角色或萝莉/正太分类目录提交 Pull Request，审核合并后定期同步至图库接口分发。

---

## 贡献者

感谢所有为 TodayWaifu 提交代码、修复问题、完善文档或提出建议的开发者：

<a href="https://github.com/MimoKit/TodayWaifu/graphs/contributors"><img src="https://contributors-img.web.app/image?repo=MimoKit/TodayWaifu&max=100" alt="TodayWaifu contributors" height="48"></a>

公开署名贡献者：  
[MimoKit](https://github.com/MimoKit) · [CWalkene](https://github.com/CWalkene) · [spaxie](https://github.com/spaxie) · [Xbaiyz12](https://github.com/Xbaiyz12) · [xiaolinlino](https://github.com/xiaolinlino) · [zory1117](https://github.com/zory1117)

---

## 开源协议与声明

- 本项目仅供个人学习与二次元社区交流使用，严禁用于任何商业牟利用途。
- 本项目遵循 **[GNU General Public License v3.0 (GPLv3)](./LICENSE)** 协议开源。
