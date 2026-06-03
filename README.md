# gs_wuwa_daily_wife

GSCore / GsUID 版鸣潮“今日老婆”插件。

## 插件目录

`gs_wuwa_daily_wife`

## 使用方法

固定触发命令：

```text
wl今日老婆
```

插件使用 GSCore 强制前缀，前缀为 `wl`，命令正文固定为 `今日老婆`。不再提供插件内自定义触发词配置。

触发后，插件会按当天日期和用户 ID 固定随机一个角色，然后：
2. 去 `gsuid_core/data/XutheringWavesUID/custom_role_pile/<数字ID>/`；
3. 随机取一张图片发送。

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
- `DailyWifeTextTemplate`：文字模板。

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