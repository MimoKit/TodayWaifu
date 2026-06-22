from __future__ import annotations

from typing import Dict

from gsuid_core.utils.plugins_config.models import (
    GSC,
    GsBoolConfig,
    GsIntConfig,
    GsStrConfig,
)

CONFIG_DEFAULT: Dict[str, GSC] = {
    'DailyWifeImageSource': GsStrConfig(
        '图片数据源',
        '选择 local 使用本地图片目录；选择 gallery 使用远程图库接口',
        'local',
        options=['local', 'gallery'],
    ),
    'DailyWifeCustomRolePilePath': GsStrConfig(
        '本地角色图片目录',
        '图片数据源为 local 时生效。留空时自动查找 gsuid_core/data/XutheringWavesUID/custom_role_pile；也可以手动填写绝对路径',
        '',
    ),
    'DailyWifeRoleMapPath': GsStrConfig(
        '本地角色 ID 对照表路径',
        '兼容旧配置。留空时今日老婆使用 wife_role_id_map.txt，今日老公使用 husband_role_id_map.txt',
        '',
    ),
    'DailyWifeWifeRoleMapPath': GsStrConfig(
        '今日老婆角色 ID 对照表路径',
        '本地和图库模式均生效。留空时使用插件内置 wife_role_id_map.txt，只抽取表内角色',
        '',
    ),
    'DailyWifeHusbandRoleMapPath': GsStrConfig(
        '今日老公角色 ID 对照表路径',
        '本地和图库模式均生效。留空时使用插件内置 husband_role_id_map.txt，只抽取表内角色',
        '',
    ),
    'DailyWifeGalleryApiUrl': GsStrConfig(
        '图库接口地址',
        'XWUID 图库角色立绘接口地址，默认使用 https://img.xlinxc.cn/api/xwuid/roles',
        'https://img.xlinxc.cn/api/xwuid/roles',
    ),
    'DailyWifeGalleryUsername': GsStrConfig(
        '图库账号',
        '访问 XWUID 图库接口和图片所需的账号',
        '',
    ),
    'DailyWifeGalleryPassword': GsStrConfig(
        '图库密码',
        '访问 XWUID 图库接口和图片所需的密码',
        '',
    ),
    'DailyWifeSendText': GsBoolConfig(
        '发送文字说明',
        '开启后图片前附带“你今天的老婆是xxx”',
        True,
    ),
    'DailyWifeReplyPrefixEnabled': GsBoolConfig(
        '启用回复前缀',
        '开启后插件回复会自动添加“[今日老婆]”前缀；关闭后不添加该前缀',
        True,
    ),
    'DailyWifeAtUser': GsBoolConfig(
        '发送时艾特触发者',
        '开启后发送今日老婆和抢老婆成功图片时会艾特对应用户',
        True,
    ),
    'DailyWifeShowRoleId': GsBoolConfig(
        '显示角色 ID',
        '开启后在文字说明里额外显示本次角色对应的数字 ID',
        False,
    ),
    'DailyWifeDebugMode': GsBoolConfig(
        'Debug模式',
        '开启后主人可无限次抽取、指定角色（命令：今日老婆 [角色名]），且不计入今日老婆列表',
        False,
    ),
    'DailyWifeTextTemplate': GsStrConfig(
        '文字模板',
        '可用变量：{name} 角色名，{role_id} 数字 ID',
        '你今天的老婆是{name}',
    ),
    'DailyWifeEnableGroupMember': GsBoolConfig(
        '今日老婆概率抽群友',
        '开启后「今日老婆」会按配置概率从本群 GSCore 成员缓存里抽取群友，未命中或获取失败时仍抽鸣潮角色',
        False,
    ),
    'DailyWifeGroupMemberProbability': GsStrConfig(
        '今日老婆抽群友概率',
        '0 到 1 之间的小数，例如 0.1 表示 10% 概率抽群友；仅在开启今日老婆概率抽群友后生效',
        '0.1',
    ),
    'DailyWifeGroupMemberTextTemplate': GsStrConfig(
        '今日老婆抽群友文字模板',
        '今日老婆命中群友时的文字说明模板，可用变量：{name} 群友昵称，{user_id} 群友 QQ',
        '你今天的老婆是{name}',
    ),
    'DailyWifeMarryGroupMemberEnabled': GsBoolConfig(
        '启用娶群友',
        '开启后可使用「娶群友」命令，从本群 GSCore 成员缓存里抽取群友',
        False,
    ),
    'DailyWifeMarryGroupMemberTextTemplate': GsStrConfig(
        '娶群友文字模板',
        '「娶群友」命令的文字说明模板，可用变量：{name} 群友昵称，{user_id} 群友 QQ',
        '你娶到的群友是{name}',
    ),
    'DailyWifeMarryOwnerEnabled': GsBoolConfig(
        '启用娶群主',
        '开启后可使用「娶群主」命令；群主身份靠群主自己发过消息后被插件记下来识别，从未发言过的群主识别不到',
        False,
    ),
    'DailyWifeMarryOwnerTextTemplate': GsStrConfig(
        '娶群主文字模板',
        '「娶群主」命令的文字说明模板，可用变量：{name} 群主昵称，{user_id} 群主 QQ',
        '你娶到的群主是{name}',
    ),
    'DailyWifeHusbandEnabled': GsBoolConfig(
        '启用今日老公',
        '开启后可使用「今日老公」命令，只抽取男角色；关闭后命令不生效',
        False,
    ),
    'DailyHusbandTextTemplate': GsStrConfig(
        '今日老公文字模板',
        '今日老公的文字说明模板，可用变量：{name} 角色名，{role_id} 数字 ID',
        '你今天的老公是{name}',
    ),
    'DailyWifeRobEnabled': GsBoolConfig(
        '启用抢老婆',
        '关闭后抢老婆命令不再生效',
        True,
    ),
    'DailyWifeRobSuccessRate': GsStrConfig(
        '抢老婆成功概率',
        '0 到 1 之间的小数，例如 0.5 表示 50% 成功。普通用户每天一次，机器人主人不受次数限制',
        '0.5',
    ),
    'DailyWifeRobSuccessTemplate': GsStrConfig(
        '抢老婆成功提示',
        '可用变量：{name} 角色名，{role_id} 数字 ID，{target} 被抢用户 ID',
        '抢老婆成功！你把对方今天的老婆{name}抢过来了！',
    ),
    'DailyWifeGiftEnabled': GsBoolConfig(
        '启用送老婆',
        '开启后可使用「送老婆 @对方」命令，把自己今天的老婆送给对方',
        True,
    ),
    'DailyWifeGiftSuccessTemplate': GsStrConfig(
        '送老婆成功提示',
        '可用变量：{name} 角色名，{role_id} 数字 ID，{target} 接收方用户 ID',
        '你把今天的老婆{name}送给了对方！',
    ),
    'DailyWifeUpdateLogEnabled': GsBoolConfig(
        '启用老婆更新记录',
        '开启后可使用「老婆更新记录」命令，实时获取 GitHub 更新记录并渲染成图片',
        True,
    ),
    'DailyWifeUpdateLogApiUrl': GsStrConfig(
        '老婆更新记录接口',
        'GitHub commits API 地址，默认读取 nnlmc/TodayWaifu 最近更新记录',
        'https://api.github.com/repos/nnlmc/TodayWaifu/commits?per_page=30',
    ),
    'DailyWifeUpdateLogLimit': GsIntConfig(
        '老婆更新记录数量',
        '单次渲染最近多少条 GitHub 更新记录，建议 3-12 条',
        6,
    ),
}
